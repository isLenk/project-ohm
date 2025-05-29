import React from 'react'
import { Link } from "react-router-dom";

const NavbarItem = ({ label, to, active, icon }: { label: string; to: string; active: boolean, icon: React.ReactNode }) => {
	return (
		<li
			className="hover:border-b-2 select-none "
			style={{ borderBottom: active ? "2px solid #fbbf24" : "2px solid transparent" }}
		>
			<Link to={to} className="flex items-center gap-2">
				{icon}
				<p className={`text-sm overflow-clip ${active ? "" : "w-0"}`}>{label}</p>
			</Link>
		</li>
	);
};

export default NavbarItem