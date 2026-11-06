import { Outlet, useLocation } from 'react-router-dom'
import Sidebar from '../components/Sidebar'

// Full-bleed pages manage their own padding and scrolling.
const FULL_BLEED = ['/curator']

const Template = () => {
  const full = FULL_BLEED.includes(useLocation().pathname)
  return (
    <div className={full ? 'flex h-screen overflow-hidden' : 'flex min-h-screen'}>
      <Sidebar />
      <main className={full ? 'min-w-0 flex-1' : 'min-w-0 flex-1 px-10 py-8'}>
        <Outlet />
      </main>
    </div>
  )
}

export default Template
