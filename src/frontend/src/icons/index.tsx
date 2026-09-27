import { forwardRef, type ComponentType, type SVGProps } from 'react'
import {
  AdjustmentsHorizontalIcon as HeroAdjustmentsHorizontalIcon,
  ArrowDownTrayIcon as HeroArrowDownTrayIcon,
  ArrowLeftIcon as HeroArrowLeftIcon,
  ArrowPathIcon as HeroArrowPathIcon,
  ArrowPathRoundedSquareIcon as HeroArrowPathRoundedSquareIcon,
  ArrowRightEndOnRectangleIcon as HeroArrowRightEndOnRectangleIcon,
  ArrowRightStartOnRectangleIcon as HeroArrowRightStartOnRectangleIcon,
  ArrowTopRightOnSquareIcon as HeroArrowTopRightOnSquareIcon,
  ArrowUpIcon as HeroArrowUpIcon,
  ArrowsPointingInIcon as HeroArrowsPointingInIcon,
  ArrowsPointingOutIcon as HeroArrowsPointingOutIcon,
  BellIcon as HeroBellIcon,
  BookOpenIcon as HeroBookOpenIcon,
  CalendarDaysIcon as HeroCalendarDaysIcon,
  CalendarIcon as HeroCalendarIcon,
  ChatBubbleBottomCenterTextIcon as HeroChatBubbleBottomCenterTextIcon,
  ChatBubbleLeftIcon as HeroChatBubbleLeftIcon,
  ChatBubbleLeftRightIcon as HeroChatBubbleLeftRightIcon,
  ChatBubbleOvalLeftEllipsisIcon as HeroChatBubbleOvalLeftEllipsisIcon,
  ChatBubbleOvalLeftIcon as HeroChatBubbleOvalLeftIcon,
  CheckCircleIcon as HeroCheckCircleIcon,
  CheckIcon as HeroCheckIcon,
  ChevronDownIcon as HeroChevronDownIcon,
  ChevronLeftIcon as HeroChevronLeftIcon,
  ChevronRightIcon as HeroChevronRightIcon,
  ChevronUpIcon as HeroChevronUpIcon,
  ClipboardDocumentListIcon as HeroClipboardDocumentListIcon,
  ClockIcon as HeroClockIcon,
  CloudArrowDownIcon as HeroCloudArrowDownIcon,
  Cog6ToothIcon as HeroCog6ToothIcon,
  DocumentDuplicateIcon as HeroDocumentDuplicateIcon,
  DocumentMagnifyingGlassIcon as HeroDocumentMagnifyingGlassIcon,
  DocumentTextIcon as HeroDocumentTextIcon,
  EllipsisHorizontalIcon as HeroEllipsisHorizontalIcon,
  EllipsisVerticalIcon as HeroEllipsisVerticalIcon,
  EnvelopeIcon as HeroEnvelopeIcon,
  ExclamationCircleIcon as HeroExclamationCircleIcon,
  ExclamationTriangleIcon as HeroExclamationTriangleIcon,
  EyeIcon as HeroEyeIcon,
  FaceSmileIcon as HeroFaceSmileIcon,
  InformationCircleIcon as HeroInformationCircleIcon,
  LanguageIcon as HeroLanguageIcon,
  LinkIcon as HeroLinkIcon,
  MegaphoneIcon as HeroMegaphoneIcon,
  MicrophoneIcon as HeroMicrophoneIcon,
  MinusCircleIcon as HeroMinusCircleIcon,
  NewspaperIcon as HeroNewspaperIcon,
  PaperAirplaneIcon as HeroPaperAirplaneIcon,
  PhoneIcon as HeroPhoneIcon,
  PhotoIcon as HeroPhotoIcon,
  PlayCircleIcon as HeroPlayCircleIcon,
  PlayIcon as HeroPlayIcon,
  PlusIcon as HeroPlusIcon,
  QuestionMarkCircleIcon as HeroQuestionMarkCircleIcon,
  ShieldCheckIcon as HeroShieldCheckIcon,
  SparklesIcon as HeroSparklesIcon,
  SpeakerWaveIcon as HeroSpeakerWaveIcon,
  SpeakerXMarkIcon as HeroSpeakerXMarkIcon,
  Squares2X2Icon as HeroSquares2X2Icon,
  StopCircleIcon as HeroStopCircleIcon,
  TrashIcon as HeroTrashIcon,
  UserCircleIcon as HeroUserCircleIcon,
  UserGroupIcon as HeroUserGroupIcon,
  UserIcon as HeroUserIcon,
  UserMinusIcon as HeroUserMinusIcon,
  VideoCameraIcon as HeroVideoCameraIcon,
  VideoCameraSlashIcon as HeroVideoCameraSlashIcon,
  XMarkIcon as HeroXMarkIcon,
} from '@heroicons/react/20/solid'
import * as Custom from './customIcons'

/**
 * Application icons: Heroicons 20 solid, the family of the Mastrao platform.
 * Every icon accepts `size` (default 24, like the previous icon set) and the
 * usual SVG props; `color` sets currentColor. Add new icons here only.
 */
export type IconProps = Omit<SVGProps<SVGSVGElement>, 'ref'> & {
  size?: number | string
  title?: string
}

export type AppIconComponent = ComponentType<IconProps>

type SourceIcon = ComponentType<
  Omit<SVGProps<SVGSVGElement>, 'ref'> & { title?: string }
>

const sized = (Icon: SourceIcon): AppIconComponent => {
  const SizedIcon = forwardRef<SVGSVGElement, IconProps>(
    ({ size = 24, width, height, ...props }, ref) => (
      <Icon
        {...props}
        {...({ ref } as object)}
        width={width ?? size}
        height={height ?? size}
      />
    )
  )
  SizedIcon.displayName = `Icon(${Icon.displayName ?? Icon.name ?? 'svg'})`
  return SizedIcon as AppIconComponent
}

export const AccountBoxIcon = sized(HeroUserIcon) // RiAccountBoxFill
export const AccountIcon = sized(HeroUserCircleIcon) // RiAccountCircleFill
export const AddIcon = sized(HeroPlusIcon) // RiAddFill
export const AdjustmentsIcon = sized(HeroAdjustmentsHorizontalIcon) // RiEqualizer2Fill
export const AdminIcon = sized(HeroShieldCheckIcon) // RiAdminFill
export const ArrowLeftIcon = sized(HeroArrowLeftIcon) // RiArrowLeftFill
export const ArrowUpIcon = sized(HeroArrowUpIcon) // RiArrowUpFill
export const ArticleIcon = sized(HeroNewspaperIcon) // RiArticleFill
export const BookOpenIcon = sized(HeroBookOpenIcon) // RiBookOpenFill
export const CalendarEventIcon = sized(HeroCalendarDaysIcon) // RiCalendarEventFill
export const CalendarIcon = sized(HeroCalendarIcon) // RiCalendarFill
export const CameraSwitchIcon = sized(HeroArrowPathRoundedSquareIcon) // RiCameraSwitchFill
export const CaptionsIcon = sized(HeroChatBubbleBottomCenterTextIcon) // RiClosedCaptioningFill
export const ChatIcon = sized(HeroChatBubbleOvalLeftIcon) // RiChat1Fill
export const CheckCircleIcon = sized(HeroCheckCircleIcon) // RiCheckboxCircleFill
export const CheckIcon = sized(HeroCheckIcon) // RiCheckFill
export const ChevronDownIcon = sized(HeroChevronDownIcon) // RiArrowDownSFill
export const ChevronLeftIcon = sized(HeroChevronLeftIcon) // RiArrowLeftSFill
export const ChevronRightIcon = sized(HeroChevronRightIcon) // RiArrowRightSFill
export const ChevronUpIcon = sized(HeroChevronUpIcon) // RiArrowUpSFill
export const CloseIcon = sized(HeroXMarkIcon) // RiCloseFill
export const CopyIcon = sized(HeroDocumentDuplicateIcon) // RiFileCopyFill
export const DeleteIcon = sized(HeroTrashIcon) // RiDeleteBinFill
export const DownloadCloudIcon = sized(HeroCloudArrowDownIcon) // RiDownloadCloudFill
export const DownloadIcon = sized(HeroArrowDownTrayIcon) // RiDownload2Fill
export const DropdownIcon = sized(HeroChevronDownIcon) // RiArrowDropDownFill
export const EffectsIcon = sized(HeroSparklesIcon) // RiImageCircleAiFill
export const EmojiIcon = sized(HeroFaceSmileIcon) // RiEmotionFill
export const ErrorIcon = sized(HeroExclamationCircleIcon) // RiErrorWarningFill
export const ExternalLinkIcon = sized(HeroArrowTopRightOnSquareIcon) // RiExternalLinkFill
export const EyeIcon = sized(HeroEyeIcon) // RiEyeFill
export const FileSearchIcon = sized(HeroDocumentMagnifyingGlassIcon) // RiFileSearchFill
export const FileTextIcon = sized(HeroDocumentTextIcon) // RiFileTextFill
export const FullscreenExitIcon = sized(HeroArrowsPointingInIcon) // RiFullscreenExitFill
export const FullscreenIcon = sized(HeroArrowsPointingOutIcon) // RiFullscreenFill
export const GlassesIcon = sized(Custom.Glasses) // RiGlassesFill
export const GobletIcon = sized(Custom.Goblet) // RiGoblet2Fill
export const GroupIcon = sized(HeroUserGroupIcon) // RiGroupFill
export const HistoryIcon = sized(HeroClockIcon) // RiHistoryFill
export const HourglassIcon = sized(HeroClockIcon) // RiHourglassFill
export const ImageAddIcon = sized(HeroPhotoIcon) // RiImageAddFill
export const InfinityIcon = sized(Custom.InfinityLoop) // RiInfinityFill
export const InformationIcon = sized(HeroInformationCircleIcon) // RiInformationFill
export const KeyboardIcon = sized(Custom.Keyboard) // RiKeyboardBoxFill
export const LinkIcon = sized(HeroLinkIcon) // RiLinksFill
export const LoginIcon = sized(HeroArrowRightEndOnRectangleIcon) // RiLoginBoxFill
export const LogoutIcon = sized(HeroArrowRightStartOnRectangleIcon) // RiLogoutBoxRFill
export const MailIcon = sized(HeroEnvelopeIcon) // RiMailFill
export const MegaphoneIcon = sized(HeroMegaphoneIcon) // RiMegaphoneFill
export const MessageIcon = sized(HeroChatBubbleLeftIcon) // RiMessage2Fill
export const MicrophoneIcon = sized(HeroMicrophoneIcon) // RiMicFill
export const MicrophoneOffIcon = sized(Custom.MicrophoneSlash) // RiMicOffFill
export const MinusCircleIcon = sized(HeroMinusCircleIcon) // RiIndeterminateCircleFill
export const MoreHorizontalIcon = sized(HeroEllipsisHorizontalIcon) // RiMoreFill
export const MoreVerticalIcon = sized(HeroEllipsisVerticalIcon) // RiMore2Fill
export const NotificationIcon = sized(HeroBellIcon) // RiNotification3Fill
export const PhoneIcon = sized(HeroPhoneIcon) // RiPhoneFill
export const PictureInPictureIcon = sized(Custom.PictureInPicture) // RiPictureInPicture2Fill
export const PinIcon = sized(Custom.Pin) // RiPushpin2Fill
export const PlayCircleIcon = sized(HeroPlayCircleIcon) // RiPlayCircleFill
export const PlayIcon = sized(HeroPlayIcon) // RiPlayFill
export const QuestionIcon = sized(HeroQuestionMarkCircleIcon) // RiQuestionFill
export const RecordIcon = sized(Custom.Record) // RiRecordCircleFill
export const RetryIcon = sized(HeroArrowPathIcon) // RiResetRightFill
export const RoomsIcon = sized(HeroUserGroupIcon) // RiDoorOpenFill
export const SendIcon = sized(HeroPaperAirplaneIcon) // RiSendPlane2Fill
export const SettingsIcon = sized(HeroCog6ToothIcon) // RiSettings3Fill
export const ShareBoxIcon = sized(HeroArrowTopRightOnSquareIcon) // RiShareBoxFill
export const SpeakerIcon = sized(HeroSpeakerWaveIcon) // RiSpeakerFill
export const SpeakIcon = sized(HeroChatBubbleOvalLeftEllipsisIcon) // RiSpeakFill
export const StopCircleIcon = sized(HeroStopCircleIcon) // RiStopCircleFill
export const SummaryIcon = sized(HeroClipboardDocumentListIcon) // RiFileList3Fill
export const TimeIcon = sized(HeroClockIcon) // RiTimeFill
export const ToolsIcon = sized(HeroSquares2X2Icon) // RiShapesFill
export const TranscriptIcon = sized(HeroChatBubbleLeftRightIcon) // RiChatQuoteFill
export const TranslateIcon = sized(HeroLanguageIcon) // RiTranslate2
export const UnpinIcon = sized(Custom.PinSlash) // RiUnpinFill
export const UserMinusIcon = sized(HeroUserMinusIcon) // RiUserMinusFill
export const VideoAddIcon = sized(HeroVideoCameraIcon) // RiVideoAddFill
export const VideoChatIcon = sized(HeroVideoCameraIcon) // RiVideoChatFill
export const VideoIcon = sized(HeroVideoCameraIcon) // RiVideoFill
export const VideoOffIcon = sized(HeroVideoCameraSlashIcon) // RiVideoOffFill
export const VideoOnIcon = sized(HeroVideoCameraIcon) // RiVideoOnFill
export const VolumeDownIcon = sized(HeroSpeakerWaveIcon) // RiVolumeDownFill
export const VolumeMuteIcon = sized(HeroSpeakerXMarkIcon) // RiVolumeMuteFill
export const VolumeUpIcon = sized(HeroSpeakerWaveIcon) // RiVolumeUpFill
export const WarningIcon = sized(HeroExclamationTriangleIcon) // RiSpam2Fill
