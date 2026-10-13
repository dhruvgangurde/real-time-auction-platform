import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

function App() {
  return <main id="main" />;
}

const root = document.getElementById('root');
if (root === null) {
  throw new Error('index.html has no #root');
}
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
