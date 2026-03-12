import { create } from "zustand";
import { persist } from "zustand/middleware";

export const PRESETS = {
  cafe: {
    label: "Cafe",
    note: "Mas tolerante al ruido y mas conservador al explicar.",
  },
  privado: {
    label: "Privado",
    note: "Mas rapido y menos estricto para conversaciones limpias.",
  },
  focus: {
    label: "Focus",
    note: "Mas contexto y explicaciones un poco mas activas.",
  },
};

export const usePresetStore = create(
  persist(
    (set) => ({
      preset: "cafe",
      hydrated: false,
      setPreset: (preset) => set({ preset }),
      setHydrated: () => set({ hydrated: true }),
    }),
    {
      name: "angle-lads-preset",
      onRehydrateStorage: () => (state) => {
        state?.setHydrated();
      },
    },
  ),
);
