// The administration section (brief 9.9; UI-1, UI-2, FR-75), behind RequireRole admin. It is
// React.lazy rendered inside the guard's outlet, not a route `lazy`: the router runs a matched
// route's `lazy` before any guard renders, so that would download this chunk for anyone. The
// server still decides every call (403 FORBIDDEN without the role).
import { NavLink, useParams } from 'react-router';

import { NotFound } from '../app/NotFound';
import styles from '../discover/discover.module.css';
import { adminEnIN } from '../strings/admin-en-IN';
import own from './admin.module.css';
import { Audit } from './Audit';
import { Users } from './Users';

// the section is the rest of the path under /admin/*: picked here rather than with <Routes>, whose
// code would land in the entry's shared router chunk (the entry is at its budget)
const SECTIONS = { '': Users, users: Users, audit: Audit } as const;

export default function AdminArea() {
  const a = adminEnIN.area;
  const { '*': rest = '' } = useParams();
  const Section = SECTIONS[rest.replace(/\/$/, '') as keyof typeof SECTIONS];
  return (
    <section className={styles.page}>
      <h1 className={styles.title}>{a.title}</h1>
      <nav className={own.tabs} aria-label={a.nav}>
        <NavLink to="/admin/users">
          {a.users}
        </NavLink>
        <NavLink to="/admin/audit">{a.audit}</NavLink>
      </nav>
      {Section === undefined ? <NotFound /> : <Section />}
    </section>
  );
}
