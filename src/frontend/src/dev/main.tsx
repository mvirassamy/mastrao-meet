// A separate Vite entry, excluded from the production build and routes.
if (import.meta.env.DEV) {
  const {
    preparePreview,
    previewConfig,
    previewRoom,
    previewRoomId,
    previewScenario,
  } = await import('./previewFixtures')
  preparePreview()
  const { queryClient } = await import('@/api/queryClient')
  const { keys } = await import('@/api/queryKeys')
  queryClient.setQueryData([keys.config], previewConfig)
  queryClient.setQueryData([keys.user], false)
  if (!['error', 'loading'].includes(previewScenario))
    queryClient.setQueryData([keys.room, previewRoomId], previewRoom)
  const { userChoicesStore } = await import('@/stores/userChoices')
  userChoicesStore.audioEnabled = false
  userChoicesStore.videoEnabled = false
  const { createRoot } = await import('react-dom/client')
  const { Preview } = await import('./Preview')
  createRoot(document.getElementById('root')!).render(<Preview />)
}
