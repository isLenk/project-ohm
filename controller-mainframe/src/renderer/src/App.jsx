import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import Characters from './pages/Characters'
import Template from './pages/Template'
import Page404 from './pages/Page404'
import FilterPage from './pages/FilterPage'
import Settings from './pages/Settings'
import Modules from './components/Modules'
import Memories from './components/Memories'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Template />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/characters" element={<Characters />} />
          <Route path="/filters" element={<FilterPage />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/modules" element={<Modules />} />
          <Route path="/memories" element={<Memories />} />
          <Route path="*" element={<Page404 />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
