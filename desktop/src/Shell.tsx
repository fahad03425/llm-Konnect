import { Outlet } from 'react-router-dom';
import Sidebar from './components/Sidebar';
import TopBar from './components/TopBar';

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
        </div>
    );
};

export default Shell;
