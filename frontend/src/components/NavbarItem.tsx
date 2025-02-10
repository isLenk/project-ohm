import React from 'react'
// import { Link } from 'react-router-dom'

const NavbarItem = ({label, to}: {label: string, to: string}) => {
  return (
    <li className='hover:border-b-2'>{label}</li>
    // <li><Link to={to}>{label}</Link></li>
  )
}

export default NavbarItem