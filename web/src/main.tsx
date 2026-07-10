import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router-dom';
import '@xyflow/react/dist/style.css';
import './index.css';
import { router } from './routes';
import { installMobileZoomGuard } from './lib/mobile';
import { useAuthStore } from './stores/useAuthStore';
import { ErrorDialogHost } from './components/common/ErrorDialogHost';

installMobileZoomGuard();

// 启动时尝试用本地 token 校验登录态；失败则清空本地存储。
void useAuthStore.getState().bootstrap();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RouterProvider router={router} />
    <ErrorDialogHost />
  </StrictMode>,
);
