/**
 * Scope store — workspace / project / collection cascade.
 * Changing a parent scope resets child selections.
 */
import { create } from 'zustand';
import { adminApi, type Tenant, type Project, type Corpus } from '@/services/admin-api';

export interface ScopeState {
  workspace: string;
  project: string;
  collection: string;

  tenants: Tenant[];
  projects: Project[];
  corpora: Corpus[];

  isLoading: boolean;
  error: string | null;
}

export interface ScopeActions {
  /** Load available tenants (workspaces). */
  loadTenants: () => Promise<void>;
  /** Load projects for current workspace. */
  loadProjects: (tenantId?: string) => Promise<void>;
  /** Load corpora for current project. */
  loadCorpora: (projectId?: string) => Promise<void>;
  /** Set workspace and reset children. */
  setWorkspace: (id: string) => void;
  /** Set project and reset collection. */
  setProject: (id: string) => void;
  /** Set collection. */
  setCollection: (id: string) => void;
  /** Refresh all levels. */
  refreshAll: () => Promise<void>;
}

export const useScopeStore = create<ScopeState & ScopeActions>((set, get) => ({
  workspace: '',
  project: '',
  collection: '',
  tenants: [],
  projects: [],
  corpora: [],
  isLoading: false,
  error: null,

  loadTenants: async () => {
    set({ isLoading: true, error: null });
    try {
      const tenants = await adminApi.listTenants();
      set({ tenants, isLoading: false });
      // Auto-select if only one
      if (tenants.length === 1 && !get().workspace) {
        get().setWorkspace(tenants[0].tenant_id);
      }
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e), isLoading: false });
    }
  },

  loadProjects: async (tenantId?: string) => {
    const tid = tenantId ?? get().workspace;
    if (!tid) return;
    set({ isLoading: true, error: null });
    try {
      const projects = await adminApi.listProjects({ tenant_id: tid });
      set({ projects, isLoading: false });
      if (projects.length === 1 && !get().project) {
        get().setProject(projects[0].project_id);
      }
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e), isLoading: false });
    }
  },

  loadCorpora: async (projectId?: string) => {
    const pid = projectId ?? get().project;
    if (!pid) return;
    set({ isLoading: true, error: null });
    try {
      const corpora = await adminApi.listCorpora({ project_id: pid });
      set({ corpora, isLoading: false });
      if (corpora.length === 1 && !get().collection) {
        get().setCollection(corpora[0].corpus_id);
      }
    } catch (e) {
      set({ error: e instanceof Error ? e.message : String(e), isLoading: false });
    }
  },

  setWorkspace: (id: string) => {
    set({ workspace: id, project: '', collection: '', projects: [], corpora: [] });
    if (id) get().loadProjects(id);
  },

  setProject: (id: string) => {
    set({ project: id, collection: '', corpora: [] });
    if (id) get().loadCorpora(id);
  },

  setCollection: (id: string) => {
    set({ collection: id });
  },

  refreshAll: async () => {
    await get().loadTenants();
    const { workspace, project } = get();
    if (workspace) await get().loadProjects(workspace);
    if (project) await get().loadCorpora(project);
  },
}));
