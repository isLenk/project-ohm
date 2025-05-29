import { useEffect, useState } from 'react'
const Modules = () => {
  const [modules, setModules] = useState({ modules: {} })

  useEffect(() => {
    const fetchModules = async () => {
      console.log('Fetching modules...')
      const response = await window.api.getModules();
      console.log('Modules:', response)
      setModules({modules: response})
    }

    fetchModules()
  }, [])
  
  // const handleModuleClick = (moduleName) => {
  //   ipcRenderer.send('open-module', moduleName)
  // }

  return (
    <div>
      <h2>Modules</h2>
      <ul className='flex flex-col gap-2'>
        {Object.keys(modules["modules"]).map((moduleName) => (
          <li key={moduleName} className='p-2 border-[1px] rounded-sm '>
            <p className='capitalize'>
              {moduleName}
            </p>
            <p className='text-sm text-gray-400'>{modules["modules"][moduleName].description}</p>
          </li>
        ))}
      </ul>
    </div>
  )
}

export default Modules