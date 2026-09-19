# UI Stack V1

日期：2026-09-19

## 决策

研究台使用：

- Windmill Full-code App
- React 19
- Ant Design 6
- 原生 CSS / 少量 CSS variables

当前验证：
- Windmill Full-code App 官方支持 React 19
- Ant Design v6 要求 React >=18，原生支持 React 19
- 当前 Ant Design 最新稳定线为 6.x

## 为什么不自己写组件

研究台需要的主要是成熟内部系统组件：

- Table
- Pagination
- Input/Search
- Select
- DatePicker
- Tag
- Badge
- Statistic
- Drawer
- Tabs
- Tooltip
- Alert
- Modal
- Form
- Empty
- Skeleton
- Timeline
- Descriptions

全部由 Ant Design 提供。

## 页面组件建议

### 今日发现

- Statistic：今日热点/黑马/重点Case/成本
- List/Table：重点发现
- Tag：发现来源 / 研究层级
- Alert：数据源异常 / 预算告警

### 视频库

- Table
- server-side Pagination
- Select / DatePicker / Input.Search
- Tag
- Drawer：快速预览

### 信号/热点库

- Table
- Tabs：热点/话题/搜索/Creator
- Timeline：热度/排名历史

### Case Detail

- Descriptions：基础信息
- Statistic：当前指标
- Timeline：发现与研究过程
- Tabs：转写 / 评论 / 分析 / 人工备注
- Drawer：技术原始数据

### 账号库

- Table
- Statistic
- Timeline
- 简单趋势 Chart（V1 可后置）

## 性能原则

- 默认 server-side pagination
- 不一次加载完整历史库
- 详情按 Tab lazy load
- 大评论列表虚拟化只在实测需要时增加
- 不在前端做大规模统计；SQL 后端算好再返回

## 可读性原则

默认面向普通运营人员：

1. 先显示“为什么值得看”
2. 再显示关键数据
3. 再显示研究结论
4. 最后折叠技术字段 / raw JSON

不默认展示：
- Provider raw payload
- API request id
- 内部 rule JSON
- LLM schema JSON

## 版本

V0 可验证组合：
- React 19
- antd 6.x

正式部署时 package.json 固定精确/受控 semver，并由 lockfile 固定依赖解析。

## 后置

暂不引入：
- Ant Design Pro 全套脚手架
- 独立 Next.js
- 独立路由后端
- 独立设计系统

如未来研究台复杂度明显增加，再评估 ProComponents / chart library。
