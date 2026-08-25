import { useState } from 'react';
import { SettingsIcon } from './Icons';
import { SettingsDialog } from '../settings/SettingsDialog';
import { UserMenu } from './UserMenu';
import { selectIsAdmin, useAuthStore } from '../../stores/useAuthStore';
import { Link } from 'react-router-dom';
import { BookOpen } from 'lucide-react';

/** Home page top nav (PRD §7.1) */
export function TopNav() {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const isAdmin = useAuthStore(selectIsAdmin);

  return (
    <header className="h-16 px-6 flex items-center justify-between border-b border-black/5 bg-white/60 backdrop-blur sticky top-0 z-20">
      <span className="text-xl font-semibold tracking-tight">Mira</span>
      <div className="flex items-center gap-4 text-black/55">
        <Link to="/wiki" className="flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm hover:bg-black/5 hover:text-black" aria-label="打开 Wiki">
          <BookOpen className="h-4 w-4" />
          <span className="hidden sm:inline">Wiki</span>
        </Link>
        {isAdmin && (
          <button className="p-1.5 rounded-full hover:bg-black/5" aria-label="设置" onClick={() => setSettingsOpen(true)}>
            <SettingsIcon className="w-5 h-5" />
          </button>
        )}
        <UserMenu iconClassName="w-5 h-5" />
      </div>
      {isAdmin && <SettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} />}
    </header>
  );
}
