import React from 'react'
import NavbarItem from './NavbarItem'
import { Link, useLocation } from "react-router-dom";

const Header = () => {
	const { pathname } = useLocation();
	return (
		<div className="flex justify-between items-center">
			<Link to="/">
				<h1 className="text-2xl uppercase  tracking-widest font-semibold text-gray-300">Virtual Assistant</h1>
			</Link>
			<ul className="flex gap-4 text-white">
				<NavbarItem label="Dashboard" to="/" active={pathname === "/"} />
				<NavbarItem label="Characters" to="/characters" active={pathname === "/characters"} />
				<NavbarItem label="Modules" to="/modules" active={pathname === "/modules"} />
				<NavbarItem label="Memories" to="/memories" active={pathname === "/memories"} />
				<NavbarItem label="Filters" to="/filters" active={pathname === "/filters"} />
			</ul>
		</div>
	);
};

export default Header