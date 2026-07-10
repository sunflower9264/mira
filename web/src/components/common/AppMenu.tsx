// 头部三点菜单：编辑应用 / 查看历史版本。

import { Menu, MenuButton, MenuItem, MenuItems } from '@headlessui/react';
import { DotsIcon } from './Icons';

interface AppMenuProps {
  onEdit(): void;
  onHistory(): void;
}

export function AppMenu({ onEdit, onHistory }: AppMenuProps) {
  return (
    <Menu as="div" className="relative">
      <MenuButton
        className="rounded-full p-1.5 text-black/65 transition hover:bg-black/5 data-[open]:bg-black/5"
        aria-label="菜单"
      >
        <DotsIcon className="w-4 h-4" />
      </MenuButton>
      <MenuItems
        anchor="bottom end"
        className="z-50 mt-2 w-44 rounded-2xl bg-white p-1 shadow-[0_20px_70px_rgba(0,0,0,0.18)] ring-1 ring-black/5 focus:outline-none"
      >
        <MenuItem>
          {({ focus }) => (
            <button
              type="button"
              onClick={onEdit}
              className={`w-full rounded-xl px-3 py-2 text-left text-sm text-black ${
                focus ? 'bg-black/5' : ''
              }`}
            >
              编辑
            </button>
          )}
        </MenuItem>
        <MenuItem>
          {({ focus }) => (
            <button
              type="button"
              onClick={onHistory}
              className={`w-full rounded-xl px-3 py-2 text-left text-sm text-black ${
                focus ? 'bg-black/5' : ''
              }`}
            >
              查看历史版本
            </button>
          )}
        </MenuItem>
      </MenuItems>
    </Menu>
  );
}
