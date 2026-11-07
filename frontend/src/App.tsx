import { NavLink, Route, Routes } from 'react-router-dom'
import Home from './pages/Home'
import DataExplorer from './pages/DataExplorer'

export default function App() {
  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">Zeroday</span>
        <nav>
          <NavLink to="/" end>Agent</NavLink>
          <NavLink to="/data">PNC data</NavLink>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/data" element={<DataExplorer />} />
        </Routes>
      </main>
    </div>
  )
}
