import React, { ReactNode } from 'react'
import { Alert, ConfigProvider, Select, Tag } from 'antd'
import './shell.css'
import { useProjectScope } from './src/projectScope'

export type ResearchView =
  | 'home'
  | 'briefs'
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
  { icon: '◎', label: '研究任务', view: 'briefs', enabled: true },
  { icon: '▱', label: '平台总览', view: 'platforms', enabled: false },
  { icon: '♨', label: '今日研判', view: 'today', enabled: true },
  { icon: '▶', label: '视频库', view: 'videos', enabled: true },
  { icon: '♟', label: '账号库', view: 'accounts', enabled: true },
  { icon: '◆', label: '热点库', view: 'hotspots', enabled: true },
  { icon: '▤', label: '历史研究', view: 'history', enabled: false },
  { icon: '★', label: '收藏专题', view: 'collections', enabled: false },
  { icon: '▥', label: '跨平台对比', view: 'compare', enabled: false },
  { icon: '◔', label: '运行与成本', view: 'cost', enabled: true },
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
  const { scope, projects, legacyAdmin, loading, error, chooseScope } = useProjectScope()
  const projectMode = scope?.mode === 'project'
  const allowedViews = projectMode
    ? new Set<ResearchView>(['videos', 'briefs', 'accounts'])
    : scope
      ? null
      : new Set<ResearchView>()
  const scopeValue = scope?.mode === 'project' ? `project:${scope.projectId}` : scope?.mode === 'legacy-admin' ? 'legacy-admin' : undefined

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
            {navItems.filter((item) => item.enabled && (!allowedViews || allowedViews.has(item.view))).map((item) => (
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
            <div className="top-actions">
              <div className="project-scope-control" aria-label="当前研究范围">
                <span>研究范围</span>
                <Select
                  aria-label="选择研究项目"
                  className="project-scope-select"
                  value={scopeValue}
                  loading={loading}
                  placeholder={loading ? '正在加载可见项目…' : '请选择项目'}
                  options={[
                    ...projects.map((project) => ({
                      value: `project:${project.id}`,
                      label: project.organization_name ? `${project.name} · ${project.organization_name}` : project.name,
                    })),
                    ...(legacyAdmin ? [{ value: 'legacy-admin', label: '全局历史库（管理员）' }] : []),
                  ]}
                  onChange={(value) => {
                    if (value === 'legacy-admin') {
                      chooseScope({ mode: 'legacy-admin' })
                      return
                    }
                    const project = projects.find((item) => `project:${item.id}` === value)
                    if (project) chooseScope({ mode: 'project', projectId: project.id, projectName: project.name })
                  }}
                />
                {scope?.mode === 'legacy-admin' ? <Tag color="gold">管理员历史范围</Tag> : null}
              </div>
              {actions ? <div className="top-actions-inline">{actions}</div> : null}
            </div>
          </header>

          {error ? <Alert className="project-scope-error" type="error" showIcon message="项目范围加载失败" description="未加载任何旧数据，请刷新页面后重试。" /> : null}

          {children}
        </main>
      </div>
    </ConfigProvider>
  )
}
