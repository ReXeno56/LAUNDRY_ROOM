/**
 * @since 7.1.0
 */
export var EventAvailability;
(function (EventAvailability) {
    /**
     * @platform iOS
     * @since 7.1.0
     */
    EventAvailability[EventAvailability["NOT_SUPPORTED"] = -1] = "NOT_SUPPORTED";
    /**
     * @platform Android, iOS
     * @since 7.1.0
     */
    EventAvailability[EventAvailability["BUSY"] = 0] = "BUSY";
    /**
     * @platform Android, iOS
     * @since 7.1.0
     */
    EventAvailability[EventAvailability["FREE"] = 1] = "FREE";
    /**
     * @platform Android, iOS
     * @since 7.1.0
     */
    EventAvailability[EventAvailability["TENTATIVE"] = 2] = "TENTATIVE";
    /**
     * @platform iOS
     * @since 7.1.0
     */
    EventAvailability[EventAvailability["UNAVAILABLE"] = 3] = "UNAVAILABLE";
})(EventAvailability || (EventAvailability = {}));
//# sourceMappingURL=event-availability.js.map