import React from 'react'
import NavbarItem from './NavbarItem'
import { Link, useLocation } from "react-router-dom";


import { MdSpaceDashboard } from "react-icons/md";
import { IoMdPeople } from "react-icons/io";
import { RiFunctionAddFill } from "react-icons/ri";
import { BiSolidMemoryCard } from "react-icons/bi";
import { FaFilter } from "react-icons/fa";
import { IoIosSettings } from "react-icons/io";
import Logo from '../assets/icon.svg?react'

const Header = () => {
  const { pathname } = useLocation()
  return (
    <div className="top-0 sticky z-100 flex justify-between items-center">
      <Link to="/" className="flex items-center text-white gap-4">
        <Logo className="w-6 h-6 mr-2" />
        <h1 className="text-2xl uppercase  tracking-widest font-semibold text-gray-300">
          OHM - Interface
        </h1>
      </Link>
      <ul className="flex gap-4 text-white">
        <NavbarItem
          label="Dashboard"
          to="/"
          active={pathname === '/'}
          icon={<MdSpaceDashboard />}
        />
        <NavbarItem
          label="Characters"
          to="/characters"
          active={pathname === '/characters'}
          icon={<IoMdPeople />}
        />
        <NavbarItem
          label="Modules"
          to="/modules"
          active={pathname === '/modules'}
          icon={<RiFunctionAddFill />}
        />
        <NavbarItem
          label="Memories"
          to="/memories"
          active={pathname === '/memories'}
          icon={<BiSolidMemoryCard />}
        />
        <NavbarItem
          label="Filters"
          to="/filters"
          active={pathname === '/filters'}
          icon={<FaFilter />}
        />
        <NavbarItem
          label="Settings"
          to="/settings"
          active={pathname === '/settings'}
          icon={<IoIosSettings />}
        />
      </ul>
    </div>
  )
}

export default Header