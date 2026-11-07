import { Outlet } from 'react-router-dom'
import Sidebar from '../components/Sidebar'

// The rail stays put; each page scrolls on its own and sets its own padding.
const Template = () => (
  <div className="flex h-screen overflow-hidden">
    <Sidebar />
    <main className="min-w-0 flex-1 overflow-y-auto">
      <Outlet />
    </main>
  </div>
)

export default Template
