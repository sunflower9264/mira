import { AppDialog } from './AppDialog';
import { useErrorDialogStore } from '../../stores/useErrorDialogStore';

export function ErrorDialogHost() {
  const dialog = useErrorDialogStore((s) => s.dialog);
  const close = useErrorDialogStore((s) => s.close);

  return (
    <AppDialog
      open={!!dialog}
      onClose={close}
      title={dialog?.title ?? '操作失败'}
      widthClassName="max-w-md"
      dismissible={false}
      footer={
        <button
          type="button"
          onClick={close}
          className="rounded-full bg-black px-4 py-2 text-sm font-medium text-white transition hover:bg-black/85"
        >
          知道了
        </button>
      }
    >
      <div className="whitespace-pre-wrap break-words text-sm leading-6 text-black/70">
        {dialog?.message}
      </div>
    </AppDialog>
  );
}
