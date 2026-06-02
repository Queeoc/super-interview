import { NavLink, Outlet } from 'react-router-dom';

import styles from './AppShell.module.css';

const NAV_ITEMS = [
  { to: '/', label: '工作台', end: true },
  { to: '/interview', label: '文字面试' },
  { to: '/resume', label: '简历中心' },
  { to: '/knowledge', label: '知识库' },
  { to: '/skills', label: 'Skills' }
];

export function AppShell() {
  return (
    <div className={styles.shell}>
      <aside className={styles.sidebar}>
        <div className={styles.brand}>
          <span className={styles.brandEyebrow}>Phase 9.2</span>
          <h1 className={styles.brandTitle}>super-interview</h1>
          <p className={styles.brandSubtitle}>Skill、简历与文字面试主流程已接入的新前端工作台</p>
        </div>

        <nav className={styles.nav} aria-label="主导航">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              end={item.end}
              to={item.to}
              className={({ isActive }) =>
                isActive ? `${styles.navLink} ${styles.navLinkActive}` : styles.navLink
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className={styles.sidebarFooter}>
          <p>默认请求携带 Cookie</p>
          <p>流式接口统一走 POST + ReadableStream</p>
          <p>报告与会话快照始终以服务端事实源为准</p>
        </div>
      </aside>

      <main className={styles.content}>
        <Outlet />
      </main>
    </div>
  );
}
