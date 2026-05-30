# 在 Mac(Apple Silicon)上装 GPT-SoVITS v2 并训练朝鲜语声音

> 配合 [GPT_SOVITS_GUIDE.md](GPT_SOVITS_GUIDE.md) 看。这里是**可照抄的 Mac 命令序列**。
> GPT-SoVITS 一直在更新,WebUI 标签名/脚本参数可能和下文略有出入 —— **以你 clone 的版本 README 为准**;主干流程(预处理 3 步 → 训 2 个模型 → 推理)不变。
> 官方:<https://github.com/RVC-Boss/GPT-SoVITS>
>
> 实话:Mac 训练走的是 **MPS + 部分算子退回 CPU**,能跑但慢、偶尔要排坑。**推理在 Mac 上没问题**。真嫌训练慢,把数据原样拷到云 GPU 训、Mac 只推理。

---

## 0. 前提(装一次)

```bash
xcode-select --install                 # Xcode 命令行工具(已装会提示跳过)
# Homebrew 没装就先装: https://brew.sh
brew install ffmpeg cmake
```
装 **Miniconda(Apple Silicon / arm64 版)**:<https://docs.conda.io/en/latest/miniconda.html>
建议 **16GB 以上统一内存**。

---

## 1. 拿代码 + 建环境

```bash
git clone https://github.com/RVC-Boss/GPT-SoVITS
cd GPT-SoVITS
conda create -n GPTSoVits python=3.10 -y
conda activate GPTSoVits
```

## 2. 安装(MPS)+ 下底模

新版有一键脚本:
```bash
bash install.sh --device MPS --source HF
```
它会装好 PyTorch(MPS)+ 依赖, 并下载 `GPT_SoVITS/pretrained_models/` 的底模。

> 如果你的版本没有 `install.sh` 或参数不同, 手动:
> ```bash
> pip install -r requirements.txt
> ```
> 然后按 README 的「Pretrained Models」把底模下到 `GPT_SoVITS/pretrained_models/`。

## 3. Mac 必设(每次开终端前,或写进 ~/.zshrc)

```bash
export PYTORCH_ENABLE_MPS_FALLBACK=1
```
让 MPS 没实现的算子退回 CPU,**防止训练中途 `not implemented for MPS` 崩溃**。

---

## 4. 放进我们的数据集

从网站 **校对页 → ⬇ 导出数据集(已通过)** 下载 `ko-tts-dataset.zip`,解压到项目里:

```bash
mkdir -p dataset_male && unzip ~/Downloads/ko-tts-dataset.zip -d dataset_male
cd dataset_male
# train.list 里是相对路径; WebUI 多要绝对路径 —— 转成绝对:
python3 - <<'PY'
import os
d = os.getcwd()
rows = [l for l in open('train.list', encoding='utf-8') if l.strip()]
with open('train.list', 'w', encoding='utf-8') as f:
    for l in rows:
        rel, rest = l.split('|', 1)
        f.write(os.path.join(d, rel) + '|' + rest)
print('done:', len(rows), '行 → 绝对路径')
PY
head -1 train.list   # 应是 /Users/.../dataset_male/wavs/xxx.wav|남성1|ko|문장
cd ..
```

## 5. 启动 WebUI

```bash
python webui.py
```
终端会给一个本地地址(如 `http://localhost:9874`),浏览器打开。
> Mac 上**半精度要关**(`is_half=False`);新版按设备自动处理,若界面有开关就选 fp32 / False。

---

## 6. 预处理(**跳过**切分 / ASR / 去伴奏 —— 我们已经做好)

进「**1-GPT-SoVITS-TTS**」→「**1A 训练集格式化**」:

- **标注文件 .list 路径** = `.../dataset_male/train.list`(绝对)
- **音频目录** = `.../dataset_male`(让相对的 `wavs/` 能找到;已转绝对就随意)
- **文本语言** = **韩文 / ko**
- 依次点这三步(各版本叫法不同,概念一致),每步跑绿再下一步:
  1. **文本特征**(读 .list 出音素/BERT)
  2. **SSL 特征 / HuBERT**(读 wav)
  3. **语义 token**

> 报「找不到文本/音频」≈ .list 路径或音频目录没对上。

## 7. 训练(两个模型,各自跑)

进「**1B 微调训练**」:

1. **SoVITS 训练**(管音色/音质):轮数用默认(如 8)起步;显存/内存不够就调小 batch size。点开始。
2. **GPT 训练**(管韵律/节奏):默认(如 15)起步。点开始。

- 实验名自取(如 `male_v1`)。
- 产物:`SoVITS_weights*/...pth` + `GPT_weights*/...ckpt`,记下路径。
- **Mac 上慢**:~11 分钟数据,SoVITS+GPT 合计可能 30 分钟~2 小时,看芯片。先跑通,别一上来堆大数据。

## 8. 推理 / 试听

进「**1C 推理**」→ 打开推理页:

1. 选 `male_v1` 的 **SoVITS(.pth)+ GPT(.ckpt)**。
2. **参考音频**:从 `dataset_male/wavs/` 挑一条**干净、有代表性**的 3~10 秒 + 它对应的文本(去 train.list 里查)。
3. 输入要合成的朝鲜语文本 → 生成 → 听。

效果不满意,基本靠**加数据 + 调轮数 + 换更好的参考音频**。

---

## 9. Mac 排坑

| 现象 | 处理 |
|---|---|
| `not implemented for MPS` 崩 | 确认 `export PYTORCH_ENABLE_MPS_FALLBACK=1` 了 |
| 巨慢 / 风扇狂转 | 正常(MPS+CPU fallback);先用 ~10 分钟数据跑通 |
| 内存爆 / 卡死 | 调小 batch size,关掉其他大程序 |
| 半精度报错 | 关 fp16(`is_half=False` / 选 fp32) |
| 韩语首次跑卡住 | 可能在下韩语 g2p/词典资源,保持联网 |
| 实在太慢 | 把 `dataset_male/`(含转好绝对路径的 train.list)整个拷到云 GPU 训,Mac 只做推理 |

---

## 10. 女声同理

采女声 → 上传时 speaker 填 `여성1` → 校对 approve → 导出 → 解压到 `dataset_female/` → 重复 6~8(实验名 `female_v1`)。两套数据、两套模型,别混。
