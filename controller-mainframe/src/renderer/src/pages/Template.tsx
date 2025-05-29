import React from 'react'
import Header from '../components/Header'
import { Outlet } from 'react-router-dom'

const Template = () => {
  return (
    <div className="bg-[#212229] w-full h-full absolute py-8 px-14">
      <Header />
      <br />
      <Outlet />
    </div>
  )
}

export default Template