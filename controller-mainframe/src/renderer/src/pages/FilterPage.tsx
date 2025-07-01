import React from 'react'
import FilterItem from '../components/FilterItem'

const FilterPage = () => {
    const filter_bleep = ["dumb", "brainless", "stupid"]
    const filter_kill = ["idiot", "suck"]
    const [blur, setBlur] = React.useState(true)
    const deleteFunc = (item: string) => {
        console.log("delete", item)
        
    }
    const switchFunc = (item: string) => {
        console.log("edit", item)

    }

    return (
        <div className='w-full h-full flex flex-col gap-4 '>

            <div>
            <h3 className='text-2xl text-gray-100'>Filters</h3>
            <p className="text-sm text-gray-600">Clicking the word swaps severity level</p>
            </div>
            <div className='w-full flex flex-col md:flex-row gap-4' >
                <section className='w-1/2'>
                <p>Censored</p>
                <ul>
                    {filter_bleep.map((bleep, index) => (
                        <FilterItem item={bleep} key={bleep} switchFunc={switchFunc} deleteFunc={deleteFunc}/>
                    ))}
                </ul>
                </section>
                <section className='w-1/2'>
                <div className='flex items-center justify-between'>
                    <p>Terminated </p>
                    <div className='flex items-center gap-2'>
                        <input type="checkbox" id="toggle-blur" onChange={
                            (e) => setBlur(e.target.checked)
                        }
                        checked={blur}
                        />
                        <label className='text-sm text-gray-600' htmlFor="toggle-blur"> (Hide)</label>
                    </div>
                </div>
                <ul className={` ${blur ? "blur-md" : ""}`}>
                    {filter_kill.map((kill, index) => (
                        <FilterItem item={kill} key={kill} switchFunc={switchFunc} deleteFunc={deleteFunc} selectable={!blur} />
                    ))}
                </ul>
                </section>
            </div>
            </div>
    )
}

export default FilterPage