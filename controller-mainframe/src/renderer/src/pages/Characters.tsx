import React from 'react'
import CharacterSlot from '../components/CharacterSlot'
import { IoMdAdd } from 'react-icons/io'
import { v4 as uuidv4 } from 'uuid'

const Characters = () => {
  const [characters, setCharacters] = React.useState([])

  React.useEffect(() => {
    const fetchCharacters = async () => {
      // ipc
      const response = await window.api.getCharacters()
      setCharacters(response)
    }

    fetchCharacters()
  }, [])

  const handleAddCharacter = async () => {
    // Logic to add a new character
    const newCharacter = {
      id: uuidv4(),
      name: 'New Character',
      created: new Date().toISOString().split('T')[0],
      status: 'Inactive',
      description: 'New character description',
      picture: 'default.png'
    }
    setCharacters([...characters, newCharacter])

    await window.api.addCharacter(newCharacter)
  }

  const handleSaveEdits = async (updatedCharacter) => {
    // ipc
    await window.api.updateCharacter(updatedCharacter)
  }

  return (
    <>
      <div className="flex items-center justify-between">
        <h1 className="text-xl text-white uppercase">Characters</h1>

        <button
          onClick={handleAddCharacter}
          className="flex items-center gap-2 text-white bg-blue-500/50 hover:bg-blue-500 cursor-pointer px-2 py-1 rounded-sm"
        >
          <IoMdAdd />
          New
        </button>
      </div>
      <br />
      <div className="">
        <div className="flex gap-2 w-full justify-stretch flex-col">
          {characters.map((character) => (
            <CharacterSlot
              key={character.id}
              character={character}
              setCharacter={(updatedCharacter) => {
                setCharacters(
                  characters.map((char) =>
                    char.id === updatedCharacter.id ? updatedCharacter : char
                  )
                )
              }}
              saveEdits={handleSaveEdits}
            />
          ))}
        </div>
      </div>
    </>
  )
}

export default Characters