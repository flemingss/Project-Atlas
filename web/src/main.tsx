import React from 'react';
import ReactDOM from 'react-dom/client';
import { App } from './app';
import './globals.css';

// Force dark mode by default (matches existing editor aesthetic)
document.documentElement.classList.add('dark');

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
