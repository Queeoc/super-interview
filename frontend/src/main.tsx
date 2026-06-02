import React from 'react';
import ReactDOM from 'react-dom/client';

import { AppRoot } from './app/AppRoot';
import './styles/index.css';

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <AppRoot />
  </React.StrictMode>
);
