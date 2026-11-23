import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.components as PlasmaComponents3
import org.kde.plasma.extras as PlasmaExtras
import org.kde.kirigami as Kirigami
import org.kde.plasma.plasma5support as P5Support

PlasmoidItem {
    id: root

    // Output of `grayjay-link status --json`; null until the first poll returns.
    property var status: null
    property string actionError: ""
    property string actionInfo: ""
    property bool busy: false
    property int execCounter: 0

    readonly property string cli: "\"$HOME/.local/bin/grayjay-link\""
    readonly property url iconSource: Qt.resolvedUrl("../icons/grayjay-link.svg")
    readonly property string linkState: status ? status.state : "loading"
    readonly property bool paired: !!(status && status.paired)
    readonly property bool connected: linkState === "connected"
    readonly property var pairing: status && status.pairing ? status.pairing : ({ state: "idle", message: "" })
    readonly property bool pairingRunning: pairing.state === "running"

    Plasmoid.icon: "grayjay-link"
    Plasmoid.status: PlasmaCore.Types.ActiveStatus
    toolTipMainText: "Grayjay Link"
    toolTipSubText: stateText()

    switchWidth: Kirigami.Units.gridUnit * 16
    switchHeight: Kirigami.Units.gridUnit * 12

    function shq(s) {
        return "'" + String(s).replace(/'/g, "'\\''") + "'";
    }

    function stateText() {
        switch (linkState) {
        case "loading": return i18n("Checking…");
        case "daemon-down": return i18n("Background service is not running");
        case "unpaired": return i18n("Not paired with Grayjay");
        case "grayjay-not-running": return i18n("Paired · Grayjay is not running");
        case "connecting": return i18n("Paired · connecting…");
        case "connected":
            return i18n("Connected to %1", status.device && status.device.name ? status.device.name : "Grayjay");
        case "rejected": return i18n("Grayjay no longer accepts this device. Pair again.");
        }
        return linkState;
    }

    function stateColor() {
        switch (linkState) {
        case "connected": return Kirigami.Theme.positiveTextColor;
        case "grayjay-not-running":
        case "connecting":
        case "unpaired": return Kirigami.Theme.neutralTextColor;
        case "loading": return Kirigami.Theme.disabledTextColor;
        }
        return Kirigami.Theme.negativeTextColor;
    }

    function run(cmd, callback) {
        // Unique trailing shell comment: the executable engine merges identical commands.
        execCounter += 1;
        const source = cmd + " # " + execCounter;
        executable.callbacks[source] = callback;
        executable.connectSource(source);
    }

    function refresh() {
        run(cli + " status --json --no-autostart", (code, out, err) => {
            try {
                root.status = JSON.parse(out);
            } catch (e) {
                root.status = { state: "daemon-down", message: (err || out || "").trim() };
            }
        });
    }

    function action(args, okText) {
        busy = true;
        actionError = "";
        actionInfo = "";
        run(cli + " " + args, (code, out, err) => {
            busy = false;
            if (code !== 0) {
                actionError = (err || out || i18n("Command failed")).trim().replace(/^error: /, "");
            } else {
                actionInfo = (out || "").trim() || okText || "";
            }
            refresh();
        });
    }

    P5Support.DataSource {
        id: executable
        engine: "executable"
        connectedSources: []
        property var callbacks: ({})
        onNewData: (sourceName, data) => {
            const cb = callbacks[sourceName];
            delete callbacks[sourceName];
            disconnectSource(sourceName);
            if (cb) {
                cb(data["exit code"], data["stdout"], data["stderr"]);
            }
        }
    }

    Timer {
        interval: root.pairingRunning ? 700 : (root.expanded ? 1500 : 5000)
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: root.refresh()
    }

    compactRepresentation: MouseArea {
        id: compact
        hoverEnabled: true
        onClicked: root.expanded = !root.expanded

        Kirigami.Icon {
            anchors.fill: parent
            source: root.iconSource
            active: compact.containsMouse
            opacity: root.connected || root.linkState === "loading" ? 1 : 0.6
        }
        Rectangle {
            visible: root.linkState !== "loading"
            width: Math.max(4, Math.round(parent.width / 4))
            height: width
            radius: width / 2
            anchors.left: parent.left
            anchors.bottom: parent.bottom
            color: root.stateColor()
            border.color: Kirigami.Theme.backgroundColor
            border.width: 1
        }
    }

    fullRepresentation: PlasmaExtras.Representation {
        Layout.minimumWidth: Kirigami.Units.gridUnit * 20
        Layout.preferredWidth: Kirigami.Units.gridUnit * 24
        Layout.minimumHeight: content.implicitHeight + Kirigami.Units.largeSpacing * 2
        collapseMarginsHint: false

        header: PlasmaExtras.PlasmoidHeading {
            RowLayout {
                anchors.fill: parent
                spacing: Kirigami.Units.smallSpacing
                Kirigami.Icon {
                    source: root.iconSource
                    Layout.preferredWidth: Kirigami.Units.iconSizes.medium
                    Layout.preferredHeight: Kirigami.Units.iconSizes.medium
                }
                ColumnLayout {
                    spacing: 0
                    Layout.fillWidth: true
                    Kirigami.Heading {
                        level: 3
                        text: "Grayjay Link"
                    }
                    RowLayout {
                        spacing: Kirigami.Units.smallSpacing
                        Rectangle {
                            width: Kirigami.Units.smallSpacing * 2
                            height: width
                            radius: width / 2
                            color: root.stateColor()
                        }
                        PlasmaComponents3.Label {
                            Layout.fillWidth: true
                            text: root.stateText()
                            elide: Text.ElideRight
                            opacity: 0.8
                        }
                    }
                }
            }
        }

        ColumnLayout {
            id: content
            anchors.fill: parent
            anchors.margins: Kirigami.Units.smallSpacing
            spacing: Kirigami.Units.largeSpacing

            // --- background service down -----------------------------------
            ColumnLayout {
                visible: root.linkState === "daemon-down"
                Layout.fillWidth: true
                PlasmaComponents3.Label {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: i18n("The grayjay-link service isn't running. It keeps the connection to Grayjay open in the background.")
                }
                PlasmaComponents3.Button {
                    icon.name: "media-playback-start"
                    text: i18n("Start service")
                    onClicked: root.run("systemctl --user enable --now grayjay-link.service", () => root.refresh())
                }
            }

            // --- pairing ---------------------------------------------------
            ColumnLayout {
                visible: root.status !== null && root.linkState !== "daemon-down"
                         && (!root.paired || root.linkState === "rejected")
                Layout.fillWidth: true
                spacing: Kirigami.Units.smallSpacing

                Kirigami.Heading {
                    level: 4
                    text: i18n("Pair with Grayjay")
                }
                PlasmaComponents3.Label {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    textFormat: Text.StyledText
                    text: i18n("1. In Grayjay, open <b>Sync</b> and copy the pairing link (the copy button next to the QR code).<br>2. Paste it below and press <b>Pair</b>.<br>3. Accept the pairing request in Grayjay.")
                }
                RowLayout {
                    Layout.fillWidth: true
                    PlasmaComponents3.TextField {
                        id: pairField
                        Layout.fillWidth: true
                        placeholderText: "grayjay://sync/…"
                        enabled: !root.pairingRunning
                        onAccepted: pairButton.clicked()
                    }
                    PlasmaComponents3.ToolButton {
                        icon.name: "edit-paste"
                        enabled: !root.pairingRunning
                        onClicked: { pairField.selectAll(); pairField.paste(); }
                        PlasmaComponents3.ToolTip.text: i18n("Paste from clipboard")
                        PlasmaComponents3.ToolTip.visible: hovered
                    }
                }
                RowLayout {
                    PlasmaComponents3.Button {
                        id: pairButton
                        icon.name: "network-connect"
                        text: i18n("Pair")
                        visible: !root.pairingRunning
                        enabled: pairField.text.trim().startsWith("grayjay://sync/") && !root.busy
                        onClicked: {
                            const link = pairField.text.trim();
                            const doPair = () => root.action("pair --no-wait " + root.shq(link), "");
                            if (root.linkState === "rejected") {
                                root.run(root.cli + " unpair", doPair);
                            } else {
                                doPair();
                            }
                        }
                    }
                    PlasmaComponents3.Button {
                        icon.name: "dialog-cancel"
                        text: i18n("Cancel")
                        visible: root.pairingRunning
                        onClicked: root.action("cancel-pair", "")
                    }
                    PlasmaComponents3.BusyIndicator {
                        visible: root.pairingRunning
                        Layout.preferredHeight: Kirigami.Units.iconSizes.medium
                        Layout.preferredWidth: Kirigami.Units.iconSizes.medium
                    }
                    PlasmaComponents3.Label {
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        visible: root.pairing.state === "running" || root.pairing.state === "failed"
                        text: root.pairing.message || ""
                        color: root.pairing.state === "failed" ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.textColor
                    }
                }
                PlasmaComponents3.Label {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    font: Kirigami.Theme.smallFont
                    opacity: 0.7
                    text: i18n("Pairing goes once through Grayjay's relay (relay.grayjay.app), the same way the phone app pairs. After that, everything stays on this computer.")
                }
            }

            // --- paired ----------------------------------------------------
            ColumnLayout {
                visible: root.paired && root.linkState !== "rejected"
                Layout.fillWidth: true
                spacing: Kirigami.Units.smallSpacing

                PlasmaComponents3.Label {
                    visible: root.pairing.state === "done"
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    color: Kirigami.Theme.positiveTextColor
                    text: root.pairing.message || ""
                }
                PlasmaComponents3.Label {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    visible: !root.connected && !!(root.status && root.status.message)
                    text: root.status && root.status.message ? root.status.message : ""
                    opacity: 0.8
                }
                Kirigami.Heading {
                    level: 4
                    text: i18n("Open a link in Grayjay")
                }
                RowLayout {
                    Layout.fillWidth: true
                    PlasmaComponents3.TextField {
                        id: urlField
                        Layout.fillWidth: true
                        placeholderText: "https://www.youtube.com/watch?v=…"
                        onAccepted: openButton.clicked()
                    }
                    PlasmaComponents3.ToolButton {
                        icon.name: "edit-paste"
                        onClicked: { urlField.selectAll(); urlField.paste(); }
                        PlasmaComponents3.ToolTip.text: i18n("Paste from clipboard")
                        PlasmaComponents3.ToolTip.visible: hovered
                    }
                    PlasmaComponents3.Button {
                        id: openButton
                        icon.name: "media-playback-start"
                        text: i18n("Open")
                        enabled: /^https?:\/\//.test(urlField.text.trim()) && !root.busy
                        onClicked: root.action("open " + root.shq(urlField.text.trim()), i18n("Sent to Grayjay."))
                    }
                }
                PlasmaComponents3.Label {
                    Layout.fillWidth: true
                    font: Kirigami.Theme.smallFont
                    opacity: 0.7
                    wrapMode: Text.WordWrap
                    text: i18n("Grayjay starts automatically if it isn't running.")
                }
                Item { Layout.preferredHeight: Kirigami.Units.smallSpacing }
                RowLayout {
                    Item { Layout.fillWidth: true }
                    PlasmaComponents3.Button {
                        icon.name: "network-disconnect"
                        text: i18n("Unpair")
                        enabled: !root.busy
                        onClicked: root.action("unpair", "")
                    }
                }
            }

            PlasmaComponents3.BusyIndicator {
                visible: root.busy && !root.pairingRunning
                Layout.alignment: Qt.AlignHCenter
            }
            PlasmaComponents3.Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                visible: root.actionError !== ""
                color: Kirigami.Theme.negativeTextColor
                text: root.actionError
            }
            PlasmaComponents3.Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                visible: root.actionInfo !== "" && root.actionError === ""
                text: root.actionInfo
            }
            Item { Layout.fillHeight: true }
        }
    }
}
