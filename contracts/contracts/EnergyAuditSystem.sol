// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/access/AccessControl.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";
import "./EcoToken.sol";
import "./EcoCertificate.sol";

contract EnergyAuditSystem is AccessControl, ReentrancyGuard {
    using ECDSA for bytes32;
    using MessageHashUtils for bytes32;

    bytes32 public constant AGGREGATOR_ROLE = keccak256("AGGREGATOR_ROLE");

    enum BuildingType {
        Residential,    // Жилой дом
        Office,         // Офис
        PublicFacility, // Общественное здание
        StreetLighting  // Уличное освещение
    }

    struct Device {
        address consumer;
        uint64 baselineWh;     // Базовый уровень потребления за период (Вт*ч)
        uint64 lastTimestamp;  // Защита от повторной отправки (replay)
        BuildingType buildingType;
        bool active;
    }

    // Компактная структура (2 слота хранения) для газоэффективности < 150 000 gas
    struct Reading {
        uint64 timestamp;
        uint64 energyWh;       // Потребление в Вт*ч
        uint32 powerW;         // Мощность в Вт
        bytes32 ipfsCidHash;   // Хеш CID сырых данных в IPFS
    }

    EcoToken public immutable ecoToken;
    EcoCertificate public immutable ecoCertificate;

    mapping(address => Device) public devices;
    mapping(address => Reading[]) private _deviceReadings;
    mapping(address => address[]) private _consumerDevices;
    mapping(address => uint256) public penaltyScore;

    event DeviceRegistered(
        address indexed device,
        address indexed consumer,
        BuildingType buildingType,
        uint64 baselineWh
    );
    event ReadingRecorded(
        address indexed device,
        address indexed consumer,
        uint64 energyWh,
        uint32 powerW,
        uint64 timestamp,
        bytes32 ipfsCidHash
    );
    event BonusAwarded(
        address indexed device,
        address indexed consumer,
        uint256 savingsBps,
        uint256 tokenAmount,
        uint256 certificateId
    );
    event PenaltyRecorded(
        address indexed device,
        address indexed consumer,
        uint256 excessBps,
        uint256 totalPenaltyScore
    );

    constructor(address admin, address _ecoToken, address _ecoCertificate) {
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(AGGREGATOR_ROLE, admin);
        ecoToken = EcoToken(_ecoToken);
        ecoCertificate = EcoCertificate(_ecoCertificate);
    }

    /// @notice Регистрация виртуального счетчика (только администратор/муниципалитет)
    function registerDevice(
        address deviceAddr,
        address consumer,
        BuildingType buildingType,
        uint64 baselineWh
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(deviceAddr != address(0) && consumer != address(0), "Invalid address");
        require(!devices[deviceAddr].active, "Device already active");
        require(baselineWh > 0, "Baseline must be > 0");

        devices[deviceAddr] = Device({
            consumer: consumer,
            baselineWh: baselineWh,
            lastTimestamp: 0,
            buildingType: buildingType,
            active: true
        });

        _consumerDevices[consumer].push(deviceAddr);
        emit DeviceRegistered(deviceAddr, consumer, buildingType, baselineWh);
    }

    /// @notice Фиксация подписанного показания счетчика (разрешено устройству или агрегатору)
    function submitReading(
        address deviceAddr,
        uint64 energyWh,
        uint32 powerW,
        uint64 timestamp,
        bytes32 ipfsCidHash,
        bytes calldata signature
    ) external {
        require(
            msg.sender == deviceAddr || hasRole(AGGREGATOR_ROLE, msg.sender),
            "Unauthorized writer"
        );

        Device storage dev = devices[deviceAddr];
        require(dev.active, "Device not active");
        require(timestamp > dev.lastTimestamp, "Stale or replayed timestamp");

        // Проверка криптографической подписи счетчика
        bytes32 dataHash = keccak256(
            abi.encodePacked(deviceAddr, energyWh, powerW, timestamp, ipfsCidHash, block.chainid)
        );
        address recovered = dataHash.toEthSignedMessageHash().recover(signature);
        require(recovered == deviceAddr, "Invalid device signature");

        dev.lastTimestamp = timestamp;
        _deviceReadings[deviceAddr].push(
            Reading({
                timestamp: timestamp,
                energyWh: energyWh,
                powerW: powerW,
                ipfsCidHash: ipfsCidHash
            })
        );

        emit ReadingRecorded(deviceAddr, dev.consumer, energyWh, powerW, timestamp, ipfsCidHash);
    }

    /// @notice Расчет бонусов/штрафов по итогам периода (10% экономии = 100 ECOT)
    function settlePeriod(
        address deviceAddr,
        uint64 periodTotalWh,
        string calldata certificateUri
    ) external onlyRole(AGGREGATOR_ROLE) nonReentrant {
        Device memory dev = devices[deviceAddr];
        require(dev.active, "Device not active");

        if (periodTotalWh < dev.baselineWh) {
            uint256 savedWh = uint256(dev.baselineWh - periodTotalWh);
            // savingsBps: 1000 = 10.00%
            uint256 savingsBps = (savedWh * 10000) / uint256(dev.baselineWh);
            // Умножение до деления (10% экономии = 100 ECOT, без потери точности)
            uint256 rewardAmount = (savedWh * 1e21) / uint256(dev.baselineWh);

            if (rewardAmount > 0) {
                ecoToken.mint(dev.consumer, rewardAmount);
            }

            uint256 certId = 0;
            // Выпуск NFT-сертификата при экономии >= 10%
            if (savingsBps >= 1000 && bytes(certificateUri).length > 0) {
                certId = ecoCertificate.mintCertificate(dev.consumer, certificateUri);
            }

            emit BonusAwarded(deviceAddr, dev.consumer, savingsBps, rewardAmount, certId);
        } else if (periodTotalWh > dev.baselineWh) {
            uint256 excessWh = uint256(periodTotalWh - dev.baselineWh);
            uint256 excessBps = (excessWh * 10000) / uint256(dev.baselineWh);

            // Фиксация штрафа при превышении > 5% (500 bps)
            if (excessBps > 500) {
                penaltyScore[dev.consumer] += excessBps / 100;
                emit PenaltyRecorded(
                    deviceAddr,
                    dev.consumer,
                    excessBps,
                    penaltyScore[dev.consumer]
                );
            }
        }
    }

    // --- Аудит-трасса (Read-only) ---

    function getConsumerDevices(address consumer) external view returns (address[] memory) {
        return _consumerDevices[consumer];
    }

    function getReadingsCount(address deviceAddr) external view returns (uint256) {
        return _deviceReadings[deviceAddr].length;
    }

    function getDeviceReadings(address deviceAddr) external view returns (Reading[] memory) {
        return _deviceReadings[deviceAddr];
    }
}