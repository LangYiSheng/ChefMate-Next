import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './styles/index.css';
import { applyAppearance } from './features/settings/appearance';

applyAppearance();
createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
