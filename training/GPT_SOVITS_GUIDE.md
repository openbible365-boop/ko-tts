# GPT-SoVITS 上手:用 ko-tts 数据集训朝鲜语男/女声

> 本仓库只管采集;训练在「另一摊」跑。本文是把 **ko-tts 导出的数据集** 喂进
> **GPT-SoVITS** 训练朝鲜语自定义声音的步骤清单 + Mac/云 GPU 注意事项。
>
> GPT-SoVITS WebUI 各版本(v2/v3/v4)标签名略有差异,**以你装的版本为准**;
> 这里讲的是不变的主干流程。官方文档:<https://github.com/RVC-Boss/GPT-SoVITS>

---

## 0. 先决与心法

- **质量天花板看数据,不看在哪训**。每个声音 **10–60 分钟干净单说话人** 就能出稳定可用的音色。
- 流程两件大事:**① 准备数据集(.list + wav)→ ② GPT-SoVITS 里预处理 + 训两个模型(SoVITS / GPT)→ ③ 推理试听**。
- 朝鲜语:GPT-SoVITS **v2 起支持 ko**,务必装 v2 及以上。

---

## 1. 用 ko-tts 导出数据集

先在采集端把某个声音的 approved 段拉成 GPT-SoVITS 格式(详见 `build_gptsovits_dataset.py`):

```bash
# 取 token(reviewer/admin 账号)
TOKEN=$(curl -s -X POST https://kr-tts.openbible.live/api/auth/login \
  -d 'username=YOU@example.com' -d 'password=YOURPASS' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# 男声
python3 training/build_gptsovits_dataset.py --token "$TOKEN" --speaker 남성1 --out ./dataset_male
# 女声
python3 training/build_gptsovits_dataset.py --token "$TOKEN" --speaker 여성1 --out ./dataset_female
```

得到:
```
dataset_male/wavs/<id>.wav        # 切片音频(单声道 24kHz)
dataset_male/train.list           # 每行: /abs/path.wav|남성1|ko|문장
```

> **采样率提示**:ko-tts 切片目前是 **24kHz**;GPT-SoVITS 内部按 32kHz 工作,会自动重采样,24k 能用但不是最佳。想要训练级保真,可以把后端 `seg_sample_rate` 调到 32000(只影响之后新切的段)——需要的话让我改。

---

## 2. 安装 GPT-SoVITS

```bash
git clone https://github.com/RVC-Boss/GPT-SoVITS
cd GPT-SoVITS
```

### A) 云 GPU(推荐用于训练那一步)
- RunPod / Vast.ai 租一张 **≥16GB 显存** 的 N 卡(3090/4090 即可),挑 PyTorch/CUDA 模板。
- 按官方 `install.sh`(Linux+CUDA)装依赖,或用官方整合包/Docker。
- 一次微调通常**几十分钟到几小时**、个位数美元。

### B) Mac(Apple Silicon)
- 官方有 Mac 安装路径(conda + PyTorch MPS)。照 README 的 macOS 段走。
- **训练在 Mac 上能跑但磕碰**:部分算子 MPS 没实现,要设环境变量退回 CPU:
  ```bash
  export PYTORCH_ENABLE_MPS_FALLBACK=1
  ```
  退回 CPU 的步骤会慢。数据量小(几十分钟)时通常仍可接受,但**预处理里的某些环节(如自带的 UVR5 去伴奏、faster-whisper ASR)在 Mac 上更容易出问题**——好在我们**不需要它们**(数据已经切好、转写好、去过音乐了),直接跳到「标注表」即可。
- **推理(合成)在 Mac 上没问题**,适合训完试听。

### 下载预训练模型
按 README 把 `GPT_SoVITS/pretrained_models/` 下的底模下全(s1/s2 底模、cnhubert、中文 bert 等)。**朝鲜语文本走 g2p 音素路径**,中文 BERT 对 ko 不起主要作用,但底模仍需就位。装好朝鲜语前端依赖(g2p 相关,见 README 的多语言说明)。

---

## 3. 启动 WebUI 并接入我们的数据

```bash
python webui.py    # 或官方整合包的启动脚本
```

我们已经有「切好 + 文本对齐」的数据,所以**跳过 WebUI 里的「切分 / ASR / 打标」环节**,直接用我们的 `.list`:

1. 在「**训练集格式化 / 1A**」一类的标签里,把**标注表路径**指到 `dataset_male/train.list`,音频目录指到 `dataset_male/wavs`。
2. 语言选 **韩文 / ko**。
3. 依次跑预处理三步(名字各版本不同,概念一致):
   - **文本 → 音素 / BERT 特征**(读 .list)
   - **SSL 特征(HuBERT)**(读 wav)
   - **语义 token**
   - 任一步报「找不到文本/音频」基本是 .list 路径或音频目录没对上。

> .list 里我们写的是 wav 的**绝对路径**,所以数据集目录别在跑的中途移动。

---

## 4. 训练两个模型

在「**微调训练 / 1B**」标签:

1. **SoVITS 训练(s2,音色/音质)**:小数据默认轮数(如 8 轮)起步。
2. **GPT 训练(s1,韵律/节奏)**:默认轮数(如 15 轮)起步。

- 男声、女声**各自一套**(各自的 .list → 各自的模型权重,起不同实验名)。
- 显存吃紧就调小 batch size。
- 产物是一对权重(SoVITS `.pth` + GPT `.ckpt`),记下路径。

---

## 5. 推理 / 试听

在「**推理 / 1C**」标签:
1. 加载刚训的 SoVITS + GPT 权重。
2. 传一段 **3–10 秒该说话人的参考音频** + 它对应文本(从你的 wav 里挑一条干净的)。
3. 输入要合成的朝鲜语文本 → 生成 → 试听。

判断标准:**像不像这个人、吐字清不清、有没有破音/机械感**。不满意基本靠**加数据 + 调轮数**。

---

## 6. 实操建议

- **先小后大**:先各 ~10 分钟跑通全流程、出 v1 试听;满意了再加到 30–60 分钟提质。
- **数据干净 > 数据多**:底噪、口水音、串音比时长更影响结果(我们的去音乐 + 句末停顿切分就是为这个)。
- **男女分开**:两套数据、两套模型,别混。
- **参考音频**很关键:推理时挑一条**清晰、有代表性**的参考,效果差异很大。

---

## 7. 我能帮的下一步

- 你定了**云 GPU**:我可以写一套「在 GPU 机器上:拉数据集 → 跑预处理 → 训练 → 导出权重」的**脚本**,少点 WebUI 手点。
- 你坚持 **Mac**:我可以帮你把 Mac 上的依赖坑、MPS fallback、要跳过的环节整理成一份**可照抄的命令序列**。
- 想要**训练级音频**:我可以把切片采样率从 24k 提到 32k(一行配置 + 重切)。

告诉我走哪条,我接着给。
