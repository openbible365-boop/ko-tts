// 登录/注册页共用的左侧品牌展示栏
const WAVE = [
  0.35, 0.6, 0.85, 0.45, 1, 0.7, 0.3, 0.9, 0.55, 0.75, 0.4, 0.95, 0.65, 0.5,
  0.8, 0.35, 0.7, 0.45, 1, 0.6, 0.3, 0.85, 0.55, 0.4, 0.9, 0.5, 0.7, 0.35,
]

export function AuthBrand() {
  return (
    <section className="auth-brand">
      <div className="auth-glow" />
      <div className="auth-glow two" />

      <div className="auth-topbar">
        <div className="auth-logo">OB</div>
        <div className="auth-wordmark">OB&nbsp;AUDIO&nbsp;LAB</div>
      </div>

      <div className="auth-core">
        <div className="auth-kicker">Audio Sampling Platform</div>
        <h1>
          OB
          <br />
          <em>音频采样系统</em>
        </h1>
        <p>
          专业级音频采集、标注与样本管理平台。高保真采样、实时波形监测，让每一段声音都精准入库。
        </p>
        <div className="auth-wave">
          {WAVE.map((h, i) => (
            <i
              key={i}
              style={{
                height: `${h * 100}%`,
                opacity: Number((0.45 + h * 0.5).toFixed(2)),
                animationDelay: `${(i * 0.06).toFixed(2)}s`,
              }}
            />
          ))}
        </div>
      </div>

      <div className="auth-stats">
        <div className="auth-stat">
          <div className="n">48kHz</div>
          <div className="l">采样精度</div>
        </div>
        <div className="auth-stat">
          <div className="n">12.6M</div>
          <div className="l">样本库</div>
        </div>
        <div className="auth-stat">
          <div className="n">99.9%</div>
          <div className="l">稳定运行</div>
        </div>
      </div>
    </section>
  )
}
