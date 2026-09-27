import { Menu as RACMenu, MenuSection } from 'react-aria-components'
import { Separator } from '@/primitives/Separator'
import { menuRecipe } from '@/primitives/menuRecipe'
import { FullScreenMenuItem } from './FullScreenMenuItem'
import { SettingsMenuItem } from './SettingsMenuItem'
import { FeedbackMenuItem } from './FeedbackMenuItem'
import { EffectsMenuItem } from './EffectsMenuItem'
import { SupportMenuItem } from './SupportMenuItem'
import { DocumentationMenuItem } from './DocumentationMenuItem'
import { TranscriptMenuItem } from './TranscriptMenuItem'
import { ScreenRecordingMenuItem } from './ScreenRecordingMenuItem'
import { PictureInPictureMenuItem } from '@/features/rooms/livekit/components/controls/Options/PictureInPictureMenuItem'

export const OptionsMenuItems = () => {
  return (
    <RACMenu
      className={menuRecipe({ density: 'app' }).root}
      style={{
        minWidth: '13rem',
        width: '15rem',
      }}
    >
      <MenuSection>
        <PictureInPictureMenuItem />
        <TranscriptMenuItem />
        <ScreenRecordingMenuItem />
        <FullScreenMenuItem />
        <EffectsMenuItem />
      </MenuSection>
      <Separator />
      <MenuSection>
        <SupportMenuItem />
        <DocumentationMenuItem />
        <FeedbackMenuItem />
        <SettingsMenuItem />
      </MenuSection>
    </RACMenu>
  )
}
