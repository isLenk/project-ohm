import { Outlet } from 'react-router-dom'
import Sidebar from '../components/Sidebar'

const Template = () => (
  <div className="flex min-h-screen">
    <Sidebar />
    <main className="min-w-0 flex-1 px-10 py-8">
      <Outlet />
    </main>
  </div>
)

export default Template
