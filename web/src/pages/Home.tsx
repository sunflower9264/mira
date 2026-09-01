import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppStore } from '../stores/useAppStore';
import { AppCard, CreateNewCard } from '../components/home/AppCard';
import { MarketSection, type CatalogItem } from '../components/home/GallerySection';
import { TopNav } from '../components/common/TopNav';
import { PlusIcon } from '../components/common/Icons';
import { ConfirmDialog } from '../components/common/ConfirmDialog';
import { PromptDialog } from '../components/common/PromptDialog';
import { showCaughtError, showErrorDialog } from '../stores/useErrorDialogStore';
import type { App } from '../types';
import { WorkspaceLibrary } from '../components/workspace/WorkspaceLibrary';

type LibraryTab = 'mine' | 'recent';
type HomeTab = 'apps' | 'workspaces';

export function Home() {
  const navigate = useNavigate();
  const { myApps, templates, market, recentRuns, loading, error, createBlank, cloneFromMarket, cloneTemplate, rename, remove, load } = useAppStore();
  const [renameTarget, setRenameTarget] = useState<App | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<App | null>(null);
  const [nextName, setNextName] = useState('');
  const [dialogBusy, setDialogBusy] = useState(false);
  const [templateImportingId, setTemplateImportingId] = useState<string | null>(null);
  const [libraryTab, setLibraryTab] = useState<LibraryTab>('mine');
  const [homeTab, setHomeTab] = useState<HomeTab>('apps');

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (error) showErrorDialog(error, '加载失败');
  }, [error]);

  const trimmedName = nextName.trim();
  const renameDisabled = useMemo(
    () => !renameTarget || trimmedName.length === 0 || trimmedName === renameTarget.name,
    [renameTarget, trimmedName],
  );
  const catalogItems = useMemo<CatalogItem[]>(
    () => [
      ...templates.map((app) => ({ app, kind: 'template' as const })),
      ...market.map((app) => ({ app, kind: 'market' as const })),
    ],
    [market, templates],
  );

  const handleCreate = async () => {
    try {
      const app = await createBlank();
      navigate(`/apps/${app.id}/editor`);
    } catch (error) {
      showCaughtError(error, '创建失败', '创建失败');
    }
  };

  const handleMarketClone = async (appId: string) => {
    try {
      const app = await cloneFromMarket(appId);
      navigate(`/apps/${app.id}/editor`);
    } catch (error) {
      showCaughtError(error, '克隆失败', '克隆失败');
    }
  };

  const handleTemplateUse = async (templateId: string) => {
    setTemplateImportingId(templateId);
    try {
      const app = await cloneTemplate(templateId);
      navigate(`/apps/${app.id}/editor`);
    } catch (error) {
      showCaughtError(error, '导入模板失败', '导入失败');
    } finally {
      setTemplateImportingId(null);
    }
  };

  const handleRecentOpen = (app: App) => {
    navigate(app.can_edit ? `/apps/${app.id}/view` : `/market/apps/${app.id}`);
  };

  const handleRenameRequest = (app: App) => {
    setRenameTarget(app);
    setNextName(app.name);
  };

  const closeRenameDialog = () => {
    if (dialogBusy) return;
    setRenameTarget(null);
    setNextName('');
  };

  const submitRename = async () => {
    if (!renameTarget || renameDisabled) return;
    setDialogBusy(true);
    try {
      await rename(renameTarget.id, trimmedName);
      setRenameTarget(null);
      setNextName('');
    } catch (error) {
      showCaughtError(error, '重命名失败', '重命名失败');
    } finally {
      setDialogBusy(false);
    }
  };

  const handleDeleteRequest = (app: App) => {
    setDeleteTarget(app);
  };

  const closeDeleteDialog = () => {
    if (dialogBusy) return;
    setDeleteTarget(null);
  };

  const submitDelete = async () => {
    if (!deleteTarget) return;
    setDialogBusy(true);
    try {
      await remove(deleteTarget.id);
      setDeleteTarget(null);
    } catch (error) {
      showCaughtError(error, '删除失败', '删除失败');
    } finally {
      setDialogBusy(false);
    }
  };

  return (
    <div className="min-h-full">
      <TopNav />
      <main className="max-w-6xl mx-auto px-6 pt-16 pb-24">
        <h1 className="text-center text-3xl font-medium leading-tight">
          {homeTab === 'apps' ? <>用自然语言创建和编辑<br />迷你 AI 应用</> : <>让 Codex 在持久项目中<br />继续完成复杂工作</>}
        </h1>

        <div role="tablist" aria-label="首页功能" className="mx-auto mt-7 flex w-fit rounded-full border border-black/5 bg-white p-1 text-sm shadow-pill">
          <button type="button" role="tab" aria-selected={homeTab === 'apps'} onClick={() => setHomeTab('apps')} className={`h-9 rounded-full px-5 transition ${homeTab === 'apps' ? 'bg-black font-medium text-white' : 'text-black/50 hover:bg-black/[0.04] hover:text-black'}`}>应用</button>
          <button type="button" role="tab" aria-selected={homeTab === 'workspaces'} onClick={() => setHomeTab('workspaces')} className={`h-9 rounded-full px-5 transition ${homeTab === 'workspaces' ? 'bg-black font-medium text-white' : 'text-black/50 hover:bg-black/[0.04] hover:text-black'}`}>工作空间</button>
        </div>

        {homeTab === 'apps' ? <section className="mt-8">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div
              role="tablist"
              aria-label="应用列表"
              className="inline-flex w-fit rounded-full bg-black/[0.05] p-1 text-sm"
            >
              <button
                type="button"
                role="tab"
                aria-selected={libraryTab === 'mine'}
                onClick={() => setLibraryTab('mine')}
                className={`h-9 rounded-full px-4 transition ${
                  libraryTab === 'mine' ? 'bg-white font-medium text-black shadow-sm' : 'text-black/55 hover:text-black'
                }`}
              >
                我的应用
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={libraryTab === 'recent'}
                onClick={() => setLibraryTab('recent')}
                className={`h-9 rounded-full px-4 transition ${
                  libraryTab === 'recent' ? 'bg-white font-medium text-black shadow-sm' : 'text-black/55 hover:text-black'
                }`}
              >
                最近使用
              </button>
            </div>
            <button
              onClick={handleCreate}
              className="inline-flex items-center gap-1.5 bg-black text-white text-sm rounded-full px-4 py-2 hover:bg-black/85"
            >
              <PlusIcon className="w-4 h-4" /> 新建应用
            </button>
          </div>
          <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-5">
            {libraryTab === 'mine' ? (
              <>
                {loading && myApps.length === 0 && (
                  <>
                    {[0, 1, 2, 3].map((i) => (
                      <div key={i} className="w-full aspect-video rounded-card bg-neutral-200/60 animate-pulse" />
                    ))}
                  </>
                )}
                {myApps.map((app) => (
                  <AppCard
                    key={app.id}
                    app={app}
                    onRequestRename={handleRenameRequest}
                    onRequestDelete={handleDeleteRequest}
                  />
                ))}
                <CreateNewCard onClick={handleCreate} />
              </>
            ) : (
              <>
                {loading && recentRuns.length === 0 ? (
                  <>
                    {[0, 1].map((i) => (
                      <div key={i} className="w-full aspect-video rounded-card bg-neutral-200/60 animate-pulse" />
                    ))}
                  </>
                ) : recentRuns.length > 0 ? (
                  recentRuns.map((app) => (
                    <AppCard
                      key={app.id}
                      app={app}
                      market={!app.can_edit}
                      hideMenu
                      onOpen={handleRecentOpen}
                    />
                  ))
                ) : (
                  <div className="flex aspect-video items-center justify-center rounded-card border border-dashed border-black/15 bg-white/45 text-sm text-black/45 sm:col-span-2 md:col-span-3 lg:col-span-4">
                    使用过的应用会显示在这里
                  </div>
                )}
              </>
            )}
          </div>
        </section> : <div className="mt-8"><WorkspaceLibrary /></div>}

        {homeTab === 'apps' && libraryTab === 'mine' && (
          <MarketSection
            items={catalogItems}
            importingId={templateImportingId}
            onUseTemplate={handleTemplateUse}
            onClone={handleMarketClone}
          />
        )}
      </main>
      <PromptDialog
        open={!!renameTarget}
        onClose={closeRenameDialog}
        onConfirm={submitRename}
        title="重命名应用"
        description="起一个清楚的名字，之后会更容易找到。"
        inputLabel="应用名称"
        value={nextName}
        onChange={setNextName}
        confirmLabel="保存"
        placeholder="我的应用"
        disabled={renameDisabled}
        busy={dialogBusy}
      />
      <ConfirmDialog
        open={!!deleteTarget}
        onClose={closeDeleteDialog}
        onConfirm={submitDelete}
        title="删除应用？"
        description={
          deleteTarget
            ? `"${deleteTarget.name}" 会从我的应用中移除；如果已有其他用户运行记录，应用会下架并保留历史数据。`
            : undefined
        }
        confirmLabel="删除"
        cancelLabel="取消"
        tone="danger"
        busy={dialogBusy}
      />
    </div>
  );
}
