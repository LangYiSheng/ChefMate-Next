import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './styles/index.css';
import { applyAppearance } from './features/settings/appearance';
import {AccountGate} from './features/account/AccountGate';

applyAppearance();
createRoot(document.getElementById('root')!).render(<StrictMode><AccountGate>{(account, refreshAccount, logout) => <App key={account.id} account={account} refreshAccount={refreshAccount} logout={logout}/>}</AccountGate></StrictMode>);
