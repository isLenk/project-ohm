import React from 'react'

const Characters = () => {
  const [characters, setCharacters] = React.useState([
    {
        name: 'Ohm',
        created: '2021-10-01',
        status: 'Alive',
        description: '...',
        picture: 'OHM.png'
    }
  ])
  
    return (
    <>
        <h1 className='text-xl text-white uppercase'>Characters</h1>
        <br/>
        <div className=''>
            <div className='flex gap-2 w-full justify-stretch'>
                {characters.map((character, index) => (
                    <>
                    <div key={index} className='bg-[#31343e] p-4 px-12 text-gray-100 w-fit rounded-md shadow-md'>
                        <img src={character.picture} alt={character.name} className='w-32 h-32 rounded-full' />
                        <h1 className='text-2xl'>{character.name}</h1>
                        <div>{
                        new Date(character.created).toLocaleDateString('en-US', {
                            year: 'numeric',
                            month: 'long',
                            day: 'numeric'
                        })}
                        </div>
                        <div className='flex items-center gap-2'>
                            <div className={`${
                                character.status === 'Alive' ? 'bg-green-400' : 'bg-red-400'
                            } rounded-full w-3 h-3`}/>
                            {character.status}</div>
                        <div>{character.description}</div>
                    </div>
                    <div className='bg-[#31343e] p-4 px-12 text-gray-100 w-fit rounded-md shadow-md flex-1 relative'>
                        <h3 className='uppercase tracking-wide font-semibold mb-2 flex gap-4 items-center'>Description <button className='bg-[#415c84] text-white uppercase text-sm px-4 py-1 rounded-md'>Save</button></h3>
                        
                        <textarea className='bg-[#25262d] rounded-md px-4 py-2 w-full' value={character.description}/>
                    </div></>
                ))}
            </div>
        </div>
    </>
  )
}

export default Characters