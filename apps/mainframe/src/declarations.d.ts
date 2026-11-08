export {}

declare global {
  interface Window {
    api: {
      getModules: () => Promise<any>
      getCharacters: () => Promise<any[]>
      addCharacter: (character: any) => Promise<any>
      updateCharacter: (character: any) => Promise<any>
      deleteCharacter: (id: string) => Promise<any>
      listVoices: () => Promise<string[]>
      importVoice: (name: string, bytes: ArrayBuffer) => Promise<string>
      loadVoice: (file: string) => Promise<{ loaded: boolean }>
      openDialog: (method: string, params: any) => Promise<any>
      getLogs: (count?: number) => Promise<any[]>
      getSearches: (character?: string, count?: number) => Promise<any[]>
      curatorConfig: () => Promise<{ url: string; token: string }>

      listModules: () => Promise<any[]>
      setModuleEnabled: (name: string, on: boolean) => Promise<any[]>
      restartModule: (name: string) => Promise<any[]>

      engineStatus: () => Promise<any>
      startEngine: () => Promise<any>
      stopEngine: () => Promise<any>
      restartEngine: () => Promise<any>
      engineOutput: () => Promise<string>
      onEngineChange: (fn: (status: any) => void) => () => void

      health: (days?: number) => Promise<any>
      nas: (method: string, path: string, body?: any) => Promise<any>
      getSettings: () => Promise<{ values: Record<string, string>; locked: string[]; file: string }>
      saveSettings: (values: Record<string, string>) => Promise<any>
    }
  }
}
