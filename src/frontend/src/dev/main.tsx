// A separate Vite entry, excluded from the production build and routes.
if (import.meta.env.DEV) {
  const {
    preparePreview,
    previewConfig,
    previewRoom,
    previewRoomId,
    previewScenario,
    previewUser,
  } = await import('./previewFixtures')
  const { getPreviewContent } = await import('./previewScenarioRoute')
  preparePreview()
  const { queryClient } = await import('@/api/queryClient')
  const { keys } = await import('@/api/queryKeys')
  queryClient.setQueryData([keys.config], previewConfig)
  // Workspace screens are signed in, as in real use; every other screen is not.
  const workspace = ['authenticated-home', 'meeting-history'].includes(
    getPreviewContent(previewScenario)
  )
  queryClient.setQueryData([keys.user], workspace ? previewUser : false)
  if (!['error', 'loading'].includes(previewScenario))
    queryClient.setQueryData([keys.room, previewRoomId], previewRoom)
  const { userChoicesStore } = await import('@/stores/userChoices')
  userChoicesStore.audioEnabled = false
  userChoicesStore.videoEnabled = false
  const { createRoot } = await import('react-dom/client')
  const { Preview } = await import('./Preview')
  createRoot(document.getElementById('root')!).render(<Preview />)
}
