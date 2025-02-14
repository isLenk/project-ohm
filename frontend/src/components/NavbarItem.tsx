import React from 'react'
import { Link } from "react-router-dom";

const NavbarItem = ({ label, to, active }: { label: string; to: string; active: boolean }) => {
	return (
		<li
			className="hover:border-b-2"
			style={{ borderBottom: active ? "2px solid #fbbf24" : "2px solid transparent" }}
		>
			<Link to={to}>{label}</Link>
		</li>
	);
};

export default NavbarItem