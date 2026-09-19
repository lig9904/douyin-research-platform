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
  { icon: '♟', label: '账号库', view: 'accounts', enabled: false },
  { icon: '◆', label: '热点库', view: 'hotspots', enabled: false },
  { icon: '▤', label: '历史研究', view: 'history', enabled: false },
  { icon: '★', label: '收藏专题', view: 'collections', enabled: false },
  { icon: '▥', label: '跨平台对比', view: 'compare', enabled: false },
  { icon: '◔', label: '成本与预算', view: 'cost', enabled: false },
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

          <nav className="nav">
            {navItems.map((item) => (
              <button
                key={item.view}
                className={activeView === item.view ? 'nav-item active' : 'nav-item'}
                onClick={() => item.enabled && onNavigate(item.view)}
                disabled={!item.enabled}
                title={!item.enabled ? '该页面将在后续阶段启用' : undefined}
              >
                <span>{item.icon}</span>{item.label}
              </button>
            ))}
          </nav>

          <div className="sidebar-quote">
            <div>从内容中</div>
            <strong>发现下一个可能</strong>
          </div>
          <div className="sidebar-foot">v1.0 · 数据事实优先</div>
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
