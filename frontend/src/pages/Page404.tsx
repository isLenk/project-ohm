import React from 'react'
import { Link } from 'react-router-dom'

const Page404 = () => {
  return (
    <div className='w-full h-full flex items-center justify-center flex-col gap-4'>
        <h1 className='text-4xl  text-gray-100'>UH OH.</h1>
        <p className='text-red-400'>I HAVE NO CLUE WHERE YOU ARE??</p>
        <Link to='/' className='text-white uppercase tracking-wide font-semibold bg-[#151515] rounded-md px-2 py-2'>Home</Link>
    </div>
  )
}

export default Page404