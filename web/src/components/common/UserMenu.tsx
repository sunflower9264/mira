import { useNavigate } from 'react-router-dom';
import { Menu, MenuButton, MenuItem, MenuItems } from '@headlessui/react';
import { useAuthStore } from '../../stores/useAuthStore';
import { UserIcon } from './Icons';

interface UserMenuProps {
  iconClassName?: string;
}

export function UserMenu({ iconClassName = 'w-5 h-5' }: UserMenuProps) {
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);

  if (!user) {
    return (
      <button
        type="button"
        onClick={() => navigate('/login')}
        className="p-1.5 rounded-full hover:bg-black/5"
        aria-label="登录"
      >
        <UserIcon className={iconClassName} />
      </button>
    );
  }

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  return (
    <Menu as="div" className="relative">
      <MenuButton
        className="inline-flex items-center gap-1.5 rounded-full pl-1.5 pr-2.5 py-1 text-sm text-black hover:bg-black/5"
        aria-label="账号菜单"
      >
        <UserIcon className={iconClassName} />
        <span className="max-w-[120px] truncate">{user.username}</span>
      </MenuButton>
      <MenuItems
        anchor="bottom end"
        className="z-50 mt-2 w-56 rounded-2xl bg-white p-1 shadow-[0_20px_70px_rgba(0,0,0,0.18)] ring-1 ring-black/5 focus:outline-none"
      >
        <div className="px-3 py-2">
          <div className="text-[11px] uppercase tracking-wider text-black/55">已登录为</div>
          <div className="mt-0.5 truncate text-sm font-medium text-black">{user.username}</div>
        </div>
        <div className="my-1 h-px bg-black/5" />
        <MenuItem>
          {({ focus }) => (
            <button
              type="button"
              onClick={handleLogout}
              className={`w-full rounded-xl px-3 py-2 text-left text-sm text-black ${
                focus ? 'bg-black/5' : ''
              }`}
            >
              登出
            </button>
          )}
        </MenuItem>
      </MenuItems>
    </Menu>
  );
}
