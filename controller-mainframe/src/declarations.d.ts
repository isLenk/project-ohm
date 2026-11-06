export {};

declare global {
  interface Window {
    api: {
      getModules: () => Promise<any>;
      openDialog: (method: string, params: any) => Promise<any>;
      curatorConfig: () => Promise<{ url: string; token: string }>;
      // add other methods if needed
    };
  }
}