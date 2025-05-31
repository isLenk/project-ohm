import React from 'react'
import { FaTrash } from "react-icons/fa";

const CharacterSlot = ({ key, character, setCharacter, saveEdits }) => {
    const deleteCharacter = async (id) => {
        console.log('Deleting character with ID:', id);
        // const confirmed = await window.api.showQuestionDialog('Delete??', 'Are you sure you want to delete this character?');
        // console.log(confirmed);
        // if (confirmed) {
        //     // Call the API to delete the character
        //     await window.api.deleteCharacter(id);
        // }
        await window.api.openDialog('show-question-dialog', {
            title: 'Delete??',
            message: 'Are you sure you want to delete this character?'
        });
    };

    return (
        <div className='flex gap-4 items-start justify-between w-full h-full '>
            <div key={key} className='bg-[#31343e] p-4 px-12 text-gray-100 flex flex-col items-center rounded-md shadow-md'>
                <div className='w-full'>
                    <FaTrash className='text-gray-400 float-right cursor-pointer opacity-10 hover:opacity-100' onClick={async () => 
                        await deleteCharacter(character.id)
                    } />
                </div>
                <img src={character.picture} className='w-32 h-32 rounded-full bg-black border-0' />
                <input type="text" className='text-2xl text-center' value={character.name} onChange={(e) => setCharacter({ ...character, name: e.target.value })} />
                <p className='w-fit '>{
                new Date(character.created).toLocaleDateString('en-US', {
                    year: 'numeric',
                    month: 'long',
                    day: 'numeric'
                })}
                </p>
                <div className='flex gap-2 items-center justify-between w-full'>
                <p className='flex items-center gap-2 w-fit'>
                    <div className={`${
                        character.status === 'Active' ? 'bg-green-400' : 'bg-red-400'
                    } rounded-full w-3 h-3`}/>
                    {character.status}</p>
                    <button className='border-1 px-2 rounded-sm border-gray-500'>Select</button>
                </div>
            </div>
            <div className='bg-[#31343e] p-4 px-12 text-gray-100 rounded-md shadow-md flex-1 relative'>
                <h3 className='uppercase tracking-wide font-semibold mb-2 flex gap-4 items-center'>Description </h3>
                <textarea className='bg-[#25262d] rounded-md px-4 py-2 w-full' value={character.description} onChange={(e) => setCharacter({ ...character, description: e.target.value })} />
                <button className='bg-[#415c84]/20 hover:bg-[#415c84] text-white uppercase text-sm px-4 py-1 rounded-md float-right'
                    onClick={() => saveEdits(character)}>Save</button>
            </div>
        </div>
        )
}

export default CharacterSlot