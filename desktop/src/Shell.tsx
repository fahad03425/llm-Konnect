import { Outlet } from 'react-router-dom';
import WindowTitleBar from './components/WindowTitleBar';
import Sidebar from './components/Sidebar';
import TopBar from './components/TopBar';
import OnboardingModal from './components/auth/OnboardingModal';
import SettingsModal from './components/settings/SettingsModal';

const Shell = () => {
    return (
        <div className="window-root">
            <WindowTitleBar />

            <div className="app-container">
                <Sidebar />

                <div className="main-wrapper">
                    <TopBar />

                    <main className="page-content">
                        <Outlet />
                    </main>
                </div>
            </div>

            {/* First-time Account Setup Wizard (triggers if !isSetupComplete) */}
            <OnboardingModal />

            {/* Global Settings & Domain Switcher */}
            <SettingsModal />
        </div>
    );
};

export default Shell;
