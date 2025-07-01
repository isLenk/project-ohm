import React from 'react'

const Memories = () => {
  return (
    <div>
      <h1 className='text-xl'>Memories</h1>
      <br/>
      <a 
        className='bg-blue-800 p-2 rounded-sm '
        href="http://localhost:6333/dashboard#/collections" target="_blank" rel="noopener noreferrer">
        Open Qdrant Dashboard
      </a>
    </div>
  )
}

export default Memories