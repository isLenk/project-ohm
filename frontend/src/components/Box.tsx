import React from 'react'

const Box = ({children}: {children: React.ReactNode}) => {
  return <div className="bg-[#2b2a30] p-2 px-4 text-gray-100 rounded-sm">{children}</div>;
}

export default Box