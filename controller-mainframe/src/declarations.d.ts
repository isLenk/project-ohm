export {};

declare global {
  interface Window {
    api: {
      getModules: () => Promise<any>;
      // add other methods if needed
    };
  }
}