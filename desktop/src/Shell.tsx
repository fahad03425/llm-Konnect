import { Outlet } from 'react-router-dom';
import Sidebar from './components/Sidebar';
import TopBar from './components/TopBar';
import OnboardingModal from './components/auth/OnboardingModal';
import SettingsModal from './components/settings/SettingsModal';

const Shell = () => {
    return (
        <div className="app-container">
            <Sidebar />

            <div className="main-wrapper">
                <TopBar />

                <main className="page-content">
                    <Outlet />
                </main>
            </div>

            {/* First-time Account Setup Wizard (triggers if !isSetupComplete) */}
            <OnboardingModal />

            {/* Global Settings & Domain Switcher */}
            <SettingsModal />
        </div>
    );
};

export default Shell;
