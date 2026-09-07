import { Navigate, useNavigate } from 'react-router-dom';
import { prefersMobileViewport } from '../../lib/mobile';
import { SettingsDialog } from '../../components/settings/SettingsDialog';
import { selectIsAdmin, useAuthStore } from '../../stores/useAuthStore';

export function MobileSettings() {
  const navigate = useNavigate();
  const mobile = prefersMobileViewport();
  const isAdmin = useAuthStore(selectIsAdmin);
  if (!isAdmin) return <Navigate to={mobile ? '/m' : '/'} replace />;
  return <SettingsDialog open mobile={mobile} onClose={() => navigate(mobile ? '/m' : '/')} />;
}
