// Ezra Studio 横版锁定 · On indigo(靛底): 白色书形 + 金色 AI 光点/字母 z
// 取自品牌设计稿(SYM + 横版 wordmark), 反白配色: 描边白、--gold 金。
function EzraLockup() {
  const gold = '#e89a2c'
  return (
    <svg
      className="ezra-lockup"
      viewBox="0 0 340 100"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label="Ezra Studio"
      style={{ color: '#ffffff' }}
    >
      <g transform="translate(-12,8)">
        {/* 顶部金色光点(AI) */}
        <polygon points="60,13 62,21 70,23 62,25 60,33 58,25 50,23 58,21" fill={gold} />
        {/* 两侧小光点 */}
        <polygon
          points="43,24.5 44.1,28.9 48.5,30 44.1,31.1 43,35.5 41.9,31.1 37.5,30 41.9,28.9"
          fill="currentColor"
        />
        <polygon
          points="77,22 78.2,26.8 83,28 78.2,29.2 77,34 75.8,29.2 71,28 75.8,26.8"
          fill="currentColor"
        />
        {/* 翻开的书 */}
        <path
          d="M60 44 C 50 38, 34 38, 26 44 L 26 74 C 34 68, 50 68, 60 74 Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="5"
          strokeLinejoin="round"
        />
        <path
          d="M60 44 C 70 38, 86 38, 94 44 L 94 74 C 86 68, 70 68, 60 74 Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="5"
          strokeLinejoin="round"
        />
        <line x1="60" y1="44" x2="60" y2="74" stroke="currentColor" strokeWidth="4" />
      </g>
      <text
        x="116"
        y="82"
        textAnchor="start"
        fontFamily="Sora, sans-serif"
        fontWeight="800"
        fontSize="30"
        letterSpacing="0.5"
      >
        <tspan fill="currentColor">E</tspan>
        <tspan fill={gold}>z</tspan>
        <tspan fill="currentColor">ra Studio</tspan>
      </text>
    </svg>
  )
}

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
        <EzraLockup />
      </div>

      <div className="auth-core">
        <div className="auth-kicker">Audio Sampling Platform</div>
        <h1>
          <em>
            音频<span className="hl">采样</span>系统
          </em>
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
