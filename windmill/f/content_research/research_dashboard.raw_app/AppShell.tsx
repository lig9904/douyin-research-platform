import React, { ReactNode } from 'react'
import { ConfigProvider } from 'antd'
import './shell.css'

export type ResearchView =
  | 'home'
  | 'platforms'
  | 'today'
  | 'videos'
  | 'accounts'
  | 'hotspots'
  | 'history'
  | 'collections'
  | 'compare'
  | 'cost'
  | 'settings'
  | 'search'

type NavItem = {
  icon: string
  label: string
  view: ResearchView
  enabled: boolean
}

const navItems: NavItem[] = [
  { icon: '⌂', label: '首页总览', view: 'home', enabled: true },
  { icon: '▱', label: '平台总览', view: 'platforms', enabled: false },
  { icon: '♨', label: '今日发现', view: 'today', enabled: false },
  { icon: '▶', label: '视频库', view: 'videos', enabled: true },
  { icon: '♟', label: '账号库', view: 'accounts', enabled: true },
  { icon: '◆', label: '热点库', view: 'hotspots', enabled: true },
  { icon: '▤', label: '历史研究', view: 'history', enabled: false },
  { icon: '★', label: '收藏专题', view: 'collections', enabled: false },
  { icon: '▥', label: '跨平台对比', view: 'compare', enabled: false },
  { icon: '◔', label: '成本与预算', view: 'cost', enabled: true },
  { icon: '⚙', label: '系统设置', view: 'settings', enabled: false },
]

export default function AppShell({
  activeView,
  onNavigate,
  title,
  subtitle,
  actions,
  children,
  mainClassName = '',
  headerClassName = '',
}: {
  activeView: ResearchView
  onNavigate: (view: ResearchView) => void
  title: string
  subtitle: string
  actions?: ReactNode
  children: ReactNode
  mainClassName?: string
  headerClassName?: string
}) {
  return (
    <ConfigProvider
      theme={{
        token: {
          colorPrimary: '#1677ff',
          borderRadius: 10,
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
        },
      }}
    >
      <div className="app-shell">
        <aside className="sidebar">
          <div className="brand">
            <div className="brand-mark"><span /><span /><span /><span /></div>
            <div>
              <strong>内容研究台</strong>
              <small>多平台洞察 · 研究资产</small>
            </div>
          </div>

          <nav className="nav" aria-label="研究台主导航">
            {navItems.filter((item) => item.enabled).map((item) => (
              <button
                key={item.view}
                className={activeView === item.view ? 'nav-item active' : 'nav-item'}
                onClick={() => onNavigate(item.view)}
              >
                <span>{item.icon}</span>{item.label}
              </button>
            ))}
          </nav>

          <div className="sidebar-foot">数据事实优先</div>
        </aside>

        <main className={`main ${mainClassName}`.trim()}>
          <header className={`topbar ${headerClassName}`.trim()}>
            <div>
              <h1>{title}</h1>
              <p>{subtitle}</p>
            </div>
            {actions ? <div className="top-actions">{actions}</div> : null}
          </header>

          {children}
        </main>
      </div>
    </ConfigProvider>
  )
}
