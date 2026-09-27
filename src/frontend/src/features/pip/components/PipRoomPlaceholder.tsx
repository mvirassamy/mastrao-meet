import { useTranslation } from 'react-i18next'
import { styled } from '@/styled-system/jsx'
import { usePictureInPicture } from '../hooks/usePictureInPicture'
import { Button, Text } from '@/primitives'

export const PipRoomPlaceholder = () => {
  const { t } = useTranslation('rooms', {
    keyPrefix: 'pictureInPicture.placeholder',
  })
  const { close } = usePictureInPicture()

  return (
    <Container>
      <img
        src="/assets/illustrations/autre-fenetre.webp"
        alt=""
        width={768}
        height={512}
        decoding="async"
        aria-hidden="true"
        style={{
          display: 'block',
          width: 'min(260px, 70%)',
          height: 'auto',
          marginBottom: '0.25rem',
          userSelect: 'none',
          pointerEvents: 'none',
        }}
      />
      <Text variant="body">{t('title')}</Text>
      <Text variant="sm" style={{ maxWidth: '312px' }}>
        {t('description')}
      </Text>
      <Button variant="default" onPress={close}>
        {t('bringBack')}
      </Button>
    </Container>
  )
}

const Container = styled('div', {
  base: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    width: '100%',
    height: '100%',
    gap: '0.5rem',
    padding: '1.5rem',
    textAlign: 'center',
    color: 'foreground',
  },
})
