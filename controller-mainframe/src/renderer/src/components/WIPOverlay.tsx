import React from 'react'
import { IoIosBuild } from "react-icons/io";

const WIPOverlay = () => {
  return (
    <div className="fixed inset-0 bg-black/50 bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-black text-white p-4 rounded shadow-md">
        <h2 className=" text-lg font-bold flex items-center gap-4">
            <IoIosBuild className="inline-block mr-2" />
            Work in Progress</h2>
        <p>This feature is still being developed.</p>
      </div>
    </div>
  )
}

export default WIPOverlay