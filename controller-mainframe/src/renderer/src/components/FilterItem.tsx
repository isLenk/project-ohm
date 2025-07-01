import React from 'react'
import { FaDeleteLeft } from "react-icons/fa6";
import { FaEdit } from "react-icons/fa";
const FilterItem = ({ item, key, switchFunc, deleteFunc, selectable=true}) => {
  return (
    <li key={key} className={`text-gray-400 p-1 ${selectable ? "" : "pointer-events-none"}`}>
        <div className="flex justify-between">
            <button onClick={() => switchFunc(item)} className='hover:text-yellow-500 cursor-pointer'>{item}</button>
            <button onClick={() => deleteFunc(item)} className='button-1 rounded-md hover:text-[#d26161]'><FaDeleteLeft /></button>
        </div>
        <div className='w-full h-[1px] bg-[#3A3B3C] my-2'></div>    
    </li>

  )
}

export default FilterItem